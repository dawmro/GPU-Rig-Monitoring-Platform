#!/usr/bin/env python3
"""
GPU Rig Monitoring Platform — Historical Data Compaction Script

Compacts old metric data into larger time buckets to save storage.
Processes in bucket-sized time windows (1 hour) for natural boundaries.

Strategy:
1. Compact child tables FIRST (no FK exclusion needed)
2. Compact parent table SECOND (FK-safe with NOT EXISTS)
3. Each batch processes exactly one bucket window (1 hour)
4. Bucket timestamp = batch start, so no overlapping timestamps

Usage:
  python manage.py compact_data --dry-run
  python manage.py compact_data --verbose
"""

import logging
import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection

logger = logging.getLogger(__name__)

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
            'gpu_util_pct': 'avg', 'gpu_temp_c': 'avg', 'fan_speed_pct': 'avg',
            'mem_used_mb': 'avg', 'mem_free_mb': 'avg', 'mem_total_mb': 'last',
            'mem_util_pct': 'avg', 'power_draw_w': 'avg', 'power_limit_w': 'last',
            'pcie_current_gen': 'last', 'pcie_max_gen': 'last',
            'pcie_current_width': 'last', 'pcie_max_width': 'last',
            'gpu_core_clock_mhz': 'avg', 'gpu_mem_clock_mhz': 'avg',
        },
        'static_fields': ['gpu_uuid', 'model', 'snapshot_id'],
    },
    {
        'table': 'metrics_storagemetric',
        'group_by': ['rig_uuid', 'device'],
        'agg_fields': {'usage_pct': 'avg', 'temp_c': 'avg', 'capacity_bytes': 'last'},
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
    {
        'table': 'metrics_dockercontainermetric',
        'group_by': ['rig_uuid', 'name'],
        'agg_fields': {'cpu_pct': 'avg', 'mem_usage_bytes': 'avg'},
        'static_fields': ['container_id'],
    },
    # Parent table LAST — FK-safe with NOT EXISTS
    {
        'table': 'metrics_metricsnapshot',
        'group_by': ['rig_uuid'],
        'agg_fields': {
            'cpu_utilization_pct': 'avg', 'cpu_temp_c': 'avg',
            'cpu_load_avg_json': 'last', 'mem_used_bytes': 'avg',
            'mem_free_bytes': 'avg', 'mem_cached_bytes': 'avg',
            'swap_used_bytes': 'avg', 'swap_total_bytes': 'last',
            'status': 'last', 'error_count': 'sum',
        },
        'static_fields': [
            'cpu_model', 'cpu_physical_cores', 'cpu_logical_cores',
            'mem_total_bytes', 'schema_version', 'agent_version',
            'software_json', 'motherboard_json',
        ],
    },
]


class Command(BaseCommand):
    help = 'Compact old metric data into larger time buckets to save storage'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
        parser.add_argument('--verbose', action='store_true', help='Show detailed per-table statistics')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be made'))

        now = timezone.now()
        cutoff = now - timedelta(days=1)

        self.stdout.write(self.style.MIGRATE_HEADING(
            'Compacting 1-minute -> 1-hour buckets (data older than 1 day)'))
        self.stdout.write('')

        for config in COMPACT_TABLES:
            self._compact_table(config, cutoff, 60, dry_run, verbose)

        self.stdout.write(self.style.SUCCESS('Compaction complete'))

    def _compact_table(self, config, cutoff, bucket_minutes, dry_run, verbose):
        """Compact a single table using bucket-sized time windows."""
        table_name = config['table']
        group_cols = ', '.join(config['group_by'])
        agg_fields = config['agg_fields']
        static_fields = config['static_fields']

        # Build SELECT clause with bucket expression
        bucket_expr = (
            f"date_trunc('hour', timestamp) + "
            f"INTERVAL '{bucket_minutes} min' * "
            f"(EXTRACT(MINUTE FROM timestamp)::int / {bucket_minutes})"
        )
        select_parts = [f"{bucket_expr} AS bucket_ts"] + list(config['group_by'])
        for f, agg in agg_fields.items():
            select_parts.append(
                f"AVG({f}) AS {f}" if agg == 'avg' else
                f"SUM({f}) AS {f}" if agg == 'sum' else
                f"MAX({f}) AS {f}" if agg == 'max' else
                f"(ARRAY_AGG({f} ORDER BY timestamp DESC))[1] AS {f}"
            )
        for f in static_fields:
            select_parts.append(f"(ARRAY_AGG({f} ORDER BY timestamp DESC))[1] AS {f}")
        select_clause = ',\n            '.join(select_parts)

        # Build insert columns
        insert_fields = ['timestamp'] + list(agg_fields.keys()) + static_fields + list(config['group_by'])
        insert_cols = ', '.join(insert_fields)
        insert_vals = ', '.join(['bucket_ts' if f == 'timestamp' else f for f in insert_fields])

        # Get total rows and oldest timestamp
        with connection.cursor() as c:
            c.execute(f"SELECT COUNT(*) FROM {table_name} WHERE timestamp < %s", [cutoff])
            total_rows = c.fetchone()[0]
            c.execute(f"SELECT MIN(timestamp) FROM {table_name} WHERE timestamp < %s", [cutoff])
            oldest = c.fetchone()[0]

        if total_rows == 0 or oldest is None:
            if verbose:
                self.stdout.write(f'  {table_name}: nothing to compact')
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
            """
        else:
            fk_where = ""

        # Process in bucket-sized windows
        batch_window = timedelta(minutes=bucket_minutes)
        total_compacted = 0
        batch_num = 0
        t_start = time.time()

        # Round oldest down to bucket boundary
        current = oldest.replace(minute=0, second=0, microsecond=0)

        while current < cutoff:
            batch_num += 1
            batch_end = min(current + batch_window, cutoff)

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
        self.stdout.write(self.style.SUCCESS(
            f'  {table_name}: compacted {total_compacted:,} rows in {batch_num} batches ({elapsed:.1f}s)'))
