#!/usr/bin/env python3
"""
GPU Rig Monitoring Platform — Historical Data Compaction Script

Compacts old metric data into larger time buckets to save storage.
3-Tier Strategy:
- Tier 1 (Raw): 0-1 day, 1-minute buckets — no compaction needed
- Tier 2 (15-min): 1-7 days, 15-minute buckets
- Tier 3 (1-hour): 7-31 days, 1-hour buckets

Processes in bucket-sized time windows for natural boundaries.
Strategy:
1. Compact child tables FIRST (no FK exclusion needed)
2. Compact parent table SECOND (FK-safe with NOT EXISTS)
3. Each batch processes exactly one bucket window
4. Bucket timestamp = batch start, so no overlapping timestamps

Usage:
  python manage.py compact_data --dry-run
  python manage.py compact_data --verbose
  python manage.py compact_data --phase tier2  # only 1-7 day compaction
  python manage.py compact_data --phase tier3  # only 7-31 day compaction
"""

import logging
import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection

logger = logging.getLogger(__name__)

# Tier configuration
TIER_1_DAYS = 1      # 0-1 day: raw 1-minute data (no compaction)
TIER_2_DAYS = 7      # 1-7 days: 15-minute buckets
TIER_3_DAYS = 31     # 7-31 days: 1-hour buckets

TIER_2_BUCKET_MINUTES = 15
TIER_3_BUCKET_MINUTES = 60

COMPACT_TABLES = [
    {
        'table': 'metrics_gpu_process',
        'group_by': ['rig_uuid', 'gpu_index', 'pid'],
        'agg_fields': {'gpu_mem_mb': 'avg'},
        'static_fields': ['name', 'type', 'snapshot_id'],
    },
    {
        'table': 'metrics_gpumetric',
        'group_by': ['rig_uuid', 'gpu_index'],
        'agg_fields': {
            'gpu_util_pct': 'avg',
            'mem_controller_util_pct': 'avg',
            'gpu_temp_c': 'avg',
            'fan_speed_pct': 'avg',
            'mem_used_mb': 'avg',
            'mem_free_mb': 'avg',
            'mem_total_mb': 'last',
            'mem_util_pct': 'avg',
            'mem_controller_util_pct': 'avg',
            'power_draw_w': 'avg',
            'power_limit_w': 'last',
            'pcie_current_gen': 'last',
            'pcie_max_gen': 'last',
            'pcie_current_width': 'last',
            'pcie_max_width': 'last',
            'gpu_core_clock_mhz': 'avg',
            'gpu_mem_clock_mhz': 'avg',
        },
        'static_fields': ['model', 'snapshot_id'],
    },
    {
        'table': 'metrics_storagemetric',
        'group_by': ['rig_uuid', 'device'],
        'agg_fields': {
            'usage_pct': 'avg', 'temp_c': 'avg', 'capacity_bytes': 'last',
            'read_bytes_delta': 'sum', 'write_bytes_delta': 'sum',
            'read_iops_delta': 'sum', 'write_iops_delta': 'sum',
            'utilization_pct': 'avg',
            'read_bytes': 'last', 'write_bytes': 'last',
            'read_iops': 'last', 'write_iops': 'last',
            'busy_time_ms': 'last',
        },
        'static_fields': ['mountpoint', 'fstype', 'smart_health', 'snapshot_id'],
    },
    {
        'table': 'metrics_networkmetric',
        'group_by': ['rig_uuid', 'interface'],
        'agg_fields': {
            'rx_bytes_delta': 'sum', 'tx_bytes_delta': 'sum',
            'rx_errors': 'sum', 'tx_errors': 'sum',
            'link_speed_mbps': 'last', 'ipv4': 'last',
        },
        'static_fields': ['snapshot_id'],
    },
    # Power readings — compact like other timeseries
    {
        'table': 'metrics_power_reading',
        'group_by': ['rig_uuid'],
        'agg_fields': {
            'gpu_power_w': 'avg',
            'cpu_power_w': 'avg',
            'other_power_w': 'avg',
            'total_power_w': 'avg',
        },
        'static_fields': ['cpu_power_source'],
    },
    # Parent table LAST — FK-safe with NOT EXISTS
    {
        'table': 'metrics_metricsnapshot',
        'group_by': ['rig_uuid'],
        'agg_fields': {
            'cpu_utilization_pct': 'avg', 'cpu_temp_c': 'avg',
            'cpu_freq_current_mhz': 'avg', 'cpu_freq_min_mhz': 'min', 'cpu_freq_max_mhz': 'max',
            'cpu_load_avg_json': 'last', 'mem_used_bytes': 'avg',
            'mem_free_bytes': 'avg', 'mem_cached_bytes': 'avg', 'mem_total_bytes': 'last',
            'swap_used_bytes': 'avg', 'swap_total_bytes': 'last',
            'uptime_s': 'max', 'status': 'last', 'error_count': 'sum',
            'cpu_power_w': 'avg', 'total_system_power_w': 'avg',
        },
        'static_fields': [
            'schema_version',
        ],
    },
]


class Command(BaseCommand):
    help = 'Compact old metric data into larger time buckets to save storage (3-tier: 15m for 1-7d, 1h for 7-31d)'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
        parser.add_argument('--verbose', action='store_true', help='Show detailed per-table statistics')
        parser.add_argument('--phase', choices=['all', 'tier2', 'tier3'], default='all',
                            help='Run specific compaction phase: tier2 (1-7d -> 15m), tier3 (7-31d -> 1h), or all (default)')
        parser.add_argument('--days', type=int, default=31,
                            help='Retention period in days (default: 31)')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']
        phase = options['phase']
        retention_days = options['days']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be made'))

        now = timezone.now()

        # Tier 3 cutoff (retention boundary)
        tier3_cutoff = now - timedelta(days=retention_days)
        
        if phase in ('all', 'tier2'):
            self._compact_tier2(now, dry_run, verbose)
        if phase in ('all', 'tier3'):
            self._compact_tier3(now, dry_run, verbose)

        self.stdout.write(self.style.SUCCESS('Compaction complete'))

    def _compact_tier2(self, now, dry_run, verbose):
        """Phase A: Compact 1-7 day old data from 1-minute to 15-minute buckets."""
        tier2_start = now - timedelta(days=TIER_2_DAYS)  # 7 days ago
        tier2_end = now - timedelta(days=TIER_1_DAYS)    # 1 day ago

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Tier 2: Compacting 1-min -> 15-min buckets (data {TIER_1_DAYS}-{TIER_2_DAYS} days old)'))
        self.stdout.write('')

        for config in COMPACT_TABLES:
            self._compact_table(
                config=config,
                window_start=tier2_start,
                window_end=tier2_end,
                bucket_minutes=TIER_2_BUCKET_MINUTES,
                dry_run=dry_run,
                verbose=verbose
            )

    def _compact_tier3(self, now, dry_run, verbose):
        """Phase B: Compact 7-31 day old data from 15-minute to 1-hour buckets."""
        tier3_start = now - timedelta(days=TIER_3_DAYS)  # 31 days ago
        tier3_end = now - timedelta(days=TIER_2_DAYS)    # 7 days ago

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Tier 3: Compacting 15-min -> 1-hour buckets (data {TIER_2_DAYS}-{TIER_3_DAYS} days old)'))
        self.stdout.write('')

        for config in COMPACT_TABLES:
            self._compact_table(
                config=config,
                window_start=tier3_start,
                window_end=tier3_end,
                bucket_minutes=TIER_3_BUCKET_MINUTES,
                dry_run=dry_run,
                verbose=verbose
            )

    def _compact_table(self, config, window_start, window_end, bucket_minutes, dry_run, verbose):
        """Compact a single table within a specific time window using bucket-sized batches."""
        table_name = config['table']
        group_cols = ', '.join(config['group_by'])
        agg_fields = config['agg_fields']
        static_fields = config['static_fields']

        # Build bucket expression based on bucket size
        bucket_expr = self._bucket_expression(bucket_minutes)
        select_parts = [f"{bucket_expr} AS bucket_ts"] + list(config['group_by'])
        for f, agg in agg_fields.items():
            select_parts.append(
                f"AVG({f}) AS {f}" if agg == 'avg' else
                f"SUM({f}) AS {f}" if agg == 'sum' else
                f"MAX({f}) AS {f}" if agg == 'max' else
                f"MIN({f}) AS {f}" if agg == 'min' else
                f"(ARRAY_AGG({f} ORDER BY timestamp DESC))[1] AS {f}"
            )
        for f in static_fields:
            select_parts.append(f"(ARRAY_AGG({f} ORDER BY timestamp DESC))[1] AS {f}")
        select_clause = ',\n            '.join(select_parts)

        # Build insert columns
        insert_fields = ['timestamp'] + list(agg_fields.keys()) + static_fields + list(config['group_by'])
        insert_cols = ', '.join(insert_fields)
        insert_vals = ', '.join(['bucket_ts' if f == 'timestamp' else f for f in insert_fields])

        # Get total rows and oldest timestamp in window
        with connection.cursor() as c:
            c.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE timestamp >= %s AND timestamp < %s",
                [window_start, window_end]
            )
            total_rows = c.fetchone()[0]
            c.execute(
                f"SELECT MIN(timestamp) FROM {table_name} WHERE timestamp >= %s AND timestamp < %s",
                [window_start, window_end]
            )
            oldest = c.fetchone()[0]

        if total_rows == 0 or oldest is None:
            if verbose:
                self.stdout.write(f'  {table_name}: nothing to compact in this window')
            return

        if verbose:
            self.stdout.write(f'  {table_name}: {total_rows:,} rows to compact')

        if dry_run:
            return

        # Build FK-safe WHERE for parent table
        if table_name == 'metrics_metricsnapshot':
            fk_where = """
                AND NOT EXISTS (SELECT 1 FROM metrics_gpumetric g WHERE g.snapshot_id = metrics_metricsnapshot.id)
                AND NOT EXISTS (SELECT 1 FROM metrics_storagemetric s WHERE s.snapshot_id = metrics_metricsnapshot.id)
                AND NOT EXISTS (SELECT 1 FROM metrics_networkmetric n WHERE n.snapshot_id = metrics_metricsnapshot.id)
                AND NOT EXISTS (SELECT 1 FROM metrics_gpu_process p WHERE p.snapshot_id = metrics_metricsnapshot.id)
            """
        else:
            fk_where = ""

        # Process in bucket-sized windows
        batch_window = timedelta(minutes=bucket_minutes)
        total_compacted = 0
        batch_num = 0
        t_start = time.time()

        # Round oldest down to bucket boundary
        current = self._round_to_bucket(oldest, bucket_minutes)

        while current < window_end:
            batch_num += 1
            batch_end = min(current + batch_window, window_end)

            where_sql = f"timestamp >= %s AND timestamp < %s {fk_where}"
            params = [current, batch_end]

            tmp_table = f"_compact_{table_name.replace('.', '_')}_{batch_num}"

            try:
                with connection.cursor() as c:
                    # Count and skip empty batches
                    c.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {where_sql}", params)
                    batch_rows = c.fetchone()[0]
                    if batch_rows == 0:
                        current = batch_end
                        continue

                    # Aggregate into temp table
                    c.execute(f"DROP TABLE IF EXISTS {tmp_table}")
                    c.execute(
                        f"CREATE TEMP TABLE {tmp_table} AS "
                        f"SELECT {select_clause} FROM {table_name} "
                        f"WHERE {where_sql} GROUP BY {group_cols}, bucket_ts",
                        params
                    )

                    # Delete originals and insert aggregated
                    c.execute(f"DELETE FROM {table_name} WHERE {where_sql}", params)
                    c.execute(f"INSERT INTO {table_name} ({insert_cols}) SELECT {insert_vals} FROM {tmp_table}")
                    c.execute(f"DROP TABLE IF EXISTS {tmp_table}")

                total_compacted += batch_rows

                if verbose and batch_num % 24 == 0:
                    elapsed = time.time() - t_start
                    rate = total_compacted / elapsed if elapsed > 0 else 0
                    self.stdout.write(
                        f'    {current.strftime("%m-%d %H:%M")}: {total_compacted:,} rows '
                        f'({batch_num} batches, {rate:,.0f} rows/s)')

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'    batch {batch_num} ({current}): FAILED — {e}'))
                try:
                    with connection.cursor() as c:
                        c.execute(f"DROP TABLE IF EXISTS {tmp_table}")
                except Exception:
                    pass
                logger.exception('Compaction failed for %s batch %d', table_name, batch_num)

            current = batch_end

        elapsed = time.time() - t_start
        tier_name = "Tier 2 (15m)" if bucket_minutes == 15 else "Tier 3 (1h)"
        self.stdout.write(self.style.SUCCESS(
            f'  {table_name}: compacted {total_compacted:,} rows in {batch_num} batches ({elapsed:.1f}s) [{tier_name}]'))

    def _bucket_expression(self, bucket_minutes):
        """Generate SQL bucket expression for given minute interval."""
        if bucket_minutes == 60:
            return "date_trunc('hour', timestamp)"
        elif bucket_minutes == 15:
            return (
                "date_trunc('hour', timestamp) + "
                "INTERVAL '15 min' * (EXTRACT(MINUTE FROM timestamp)::int / 15)"
            )
        elif bucket_minutes == 1:
            return "date_trunc('minute', timestamp)"
        else:
            raise ValueError(f"Unsupported bucket size: {bucket_minutes}")

    def _round_to_bucket(self, dt, bucket_minutes):
        """Round datetime down to bucket boundary."""
        if bucket_minutes == 60:
            return dt.replace(minute=0, second=0, microsecond=0)
        elif bucket_minutes == 15:
            minute = (dt.minute // 15) * 15
            return dt.replace(minute=minute, second=0, microsecond=0)
        elif bucket_minutes == 1:
            return dt.replace(second=0, microsecond=0)
        else:
            raise ValueError(f"Unsupported bucket size: {bucket_minutes}")