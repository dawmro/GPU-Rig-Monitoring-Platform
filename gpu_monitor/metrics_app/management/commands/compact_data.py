#!/usr/bin/env python3
"""
GPU Rig Monitoring Platform — Historical Data Compaction Script

Compacts old metric data into larger time buckets to save storage.
Processes in 1-day time windows to avoid choking on large data volumes.

Strategy:
1. Compact child tables FIRST (no FK exclusion needed)
2. Compact parent table SECOND (FK-safe because old child rows are already gone)

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

# Child tables FIRST (no FK exclusion needed)
# Parent table LAST (FK-safe because old child rows are already compacted away)
COMPACT_TABLES = [
    {
        'table': 'metrics_gpu_process',
        'group_by': ['rig_uuid', 'gpu_index', 'pid'],
        'agg_fields': {
            'gpu_mem_mb': 'avg',
        },
        'static_fields': ['name', 'type', 'snapshot_id'],
    },
    {
        'table': 'metrics_gpumetric',
        'group_by': ['rig_uuid', 'gpu_index'],
        'agg_fields': {
            'gpu_util_pct': 'avg',
            'gpu_temp_c': 'avg',
            'fan_speed_pct': 'avg',
            'mem_used_mb': 'avg',
            'mem_free_mb': 'avg',
            'mem_total_mb': 'last',
            'mem_util_pct': 'avg',
            'power_draw_w': 'avg',
            'power_limit_w': 'last',
            'pcie_current_gen': 'last',
            'pcie_max_gen': 'last',
            'pcie_current_width': 'last',
            'pcie_max_width': 'last',
            'gpu_core_clock_mhz': 'avg',
            'gpu_mem_clock_mhz': 'avg',
        },
        'static_fields': ['gpu_uuid', 'model', 'snapshot_id'],
    },
    {
        'table': 'metrics_storagemetric',
        'group_by': ['rig_uuid', 'device'],
        'agg_fields': {
            'usage_pct': 'avg',
            'temp_c': 'avg',
            'capacity_bytes': 'last',
        },
        'static_fields': ['mountpoint', 'fstype', 'smart_health', 'snapshot_id'],
    },
    {
        'table': 'metrics_networkmetric',
        'group_by': ['rig_uuid', 'interface'],
        'agg_fields': {
            'rx_bytes_delta': 'sum',
            'tx_bytes_delta': 'sum',
            'rx_errors': 'sum',
            'tx_errors': 'sum',
            'link_speed_mbps': 'last',
            'ipv4': 'last',
        },
        'static_fields': ['snapshot_id'],
    },
    {
        'table': 'metrics_dockercontainermetric',
        'group_by': ['rig_uuid', 'name'],
        'agg_fields': {
            'cpu_pct': 'avg',
            'mem_usage_bytes': 'avg',
            'mem_limit_bytes': 'last',
            'restart_count': 'max',
        },
        'static_fields': ['image', 'status', 'snapshot_id'],
    },
    {
        'table': 'metrics_ai_process',
        'group_by': ['rig_uuid', 'process_name'],
        'agg_fields': {
            'gpu_mem_used_mb': 'avg',
            'cpu_pct': 'avg',
        },
        'static_fields': ['gpu_uuid', 'pid', 'snapshot_id'],
    },
    # Parent table LAST — FK-safe because old child rows are already compacted
    {
        'table': 'metrics_metricsnapshot',
        'group_by': ['rig_uuid'],
        'agg_fields': {
            'cpu_utilization_pct': 'avg',
            'cpu_temp_c': 'avg',
            'cpu_load_avg_json': 'last',
            'mem_used_bytes': 'avg',
            'mem_free_bytes': 'avg',
            'mem_cached_bytes': 'avg',
            'swap_used_bytes': 'avg',
            'swap_total_bytes': 'last',
            'status': 'last',
            'error_count': 'sum',
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
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview without making changes')
        parser.add_argument('--verbose', action='store_true',
                            help='Show detailed per-table statistics')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be made'))

        now = timezone.now()
        cutoff_1d = now - timedelta(days=1)

        self.stdout.write(self.style.MIGRATE_HEADING(
            'Compacting 1-minute -> 1-hour buckets (data older than 1 day)'))
        self.stdout.write('')

        for config in COMPACT_TABLES:
            table_name = config['table']
            self._compact_table(table_name, config, cutoff_1d, 60, dry_run, verbose)

        self.stdout.write(self.style.SUCCESS('Compaction complete'))

    def _compact_table(self, table_name, config, cutoff, bucket_minutes, dry_run, verbose):
        """Compact a single table using 1-day time window batches."""
        group_by = config['group_by']
        agg_fields = config['agg_fields']
        static_fields = config['static_fields']
        group_cols = ', '.join(group_by)

        bucket_expr = (
            f"date_trunc('hour', timestamp) + "
            f"INTERVAL '{bucket_minutes} min' * "
            f"(EXTRACT(MINUTE FROM timestamp)::int / {bucket_minutes})"
        )

        select_parts = [f"{bucket_expr} AS bucket_ts"]
        for col in group_by:
            select_parts.append(col)
        for field, agg_type in agg_fields.items():
            if agg_type == 'avg':
                select_parts.append(f"AVG({field}) AS {field}")
            elif agg_type == 'sum':
                select_parts.append(f"SUM({field}) AS {field}")
            elif agg_type == 'last':
                select_parts.append(f"(ARRAY_AGG({field} ORDER BY timestamp DESC))[1] AS {field}")
            elif agg_type == 'max':
                select_parts.append(f"MAX({field}) AS {field}")
            elif agg_type == 'min':
                select_parts.append(f"MIN({field}) AS {field}")
        for field in static_fields:
            select_parts.append(f"(ARRAY_AGG({field} ORDER BY timestamp DESC))[1] AS {field}")

        select_clause = ',\n            '.join(select_parts)

        # Check total rows and find oldest timestamp
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE timestamp < %s", [cutoff])
            total_rows = cursor.fetchone()[0]
            cursor.execute(f"SELECT MIN(timestamp) FROM {table_name} WHERE timestamp < %s", [cutoff])
            oldest = cursor.fetchone()[0]

        if total_rows == 0 or oldest is None:
            if verbose:
                self.stdout.write(f'  {table_name}: nothing to compact')
            return

        if verbose:
            self.stdout.write(f'  {table_name}: {total_rows:,} rows to compact')

        if dry_run:
            return

        # Process in 1-day windows
        batch_window = timedelta(days=1)
        total_compacted = 0
        batch_num = 0
        t_start = time.time()

        current_start = oldest
        while current_start < cutoff:
            batch_num += 1
            current_end = min(current_start + batch_window, cutoff)

            # Build WHERE for this batch
            # For parent table, exclude rows still referenced by child tables
            # to avoid FK violations. Use NOT EXISTS for performance.
            if table_name == 'metrics_metricsnapshot':
                where_sql = f"""
                    timestamp >= %s AND timestamp < %s
                    AND NOT EXISTS (
                        SELECT 1 FROM metrics_gpumetric g WHERE g.snapshot_id = {table_name}.id
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM metrics_storagemetric s WHERE s.snapshot_id = {table_name}.id
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM metrics_networkmetric n WHERE n.snapshot_id = {table_name}.id
                    )
                """
                params = [current_start, current_end]
            else:
                where_sql = "timestamp >= %s AND timestamp < %s"
                params = [current_start, current_end]

            tmp_table = f"_compact_tmp_{table_name.replace('.', '_')}_{batch_num}"

            try:
                with connection.cursor() as cursor:
                    # Count rows in this batch
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {where_sql}", params)
                    batch_rows = cursor.fetchone()[0]

                    if batch_rows == 0:
                        current_start = current_end
                        continue

                    # Create temp table with aggregated data
                    cursor.execute(f"DROP TABLE IF EXISTS {tmp_table}")
                    cursor.execute(
                        f"CREATE TEMP TABLE {tmp_table} AS "
                        f"SELECT {select_clause} "
                        f"FROM {table_name} "
                        f"WHERE {where_sql} "
                        f"GROUP BY {group_cols}, bucket_ts",
                        params
                    )

                    # Delete any pre-existing rows at the same bucket timestamps
                    # (from previous compaction runs or overlapping windows)
                    cursor.execute(f"""
                        DELETE FROM {table_name}
                        WHERE timestamp IN (SELECT bucket_ts FROM {tmp_table})
                    """)

                    # Delete the original 1-minute rows
                    cursor.execute(f"DELETE FROM {table_name} WHERE {where_sql}", params)

                    # Insert aggregated rows
                    insert_fields = ['timestamp'] + list(agg_fields.keys()) + static_fields + group_by
                    cols = ', '.join(insert_fields)
                    vals = ', '.join(['bucket_ts' if f == 'timestamp' else f for f in insert_fields])
                    cursor.execute(f"INSERT INTO {table_name} ({cols}) SELECT {vals} FROM {tmp_table}")

                    cursor.execute(f"DROP TABLE IF EXISTS {tmp_table}")

                total_compacted += batch_rows

                if verbose:
                    elapsed = time.time() - t_start
                    rate = total_compacted / elapsed if elapsed > 0 else 0
                    self.stdout.write(
                        f'    batch {batch_num}: {current_start.strftime("%m-%d %H:%M")} → '
                        f'{current_end.strftime("%m-%d %H:%M")}: {batch_rows:,} rows '
                        f'({rate:,.0f} rows/s)')

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'    batch {batch_num}: FAILED — {e}'))
                try:
                    with connection.cursor() as c:
                        c.execute(f"DROP TABLE IF EXISTS {tmp_table}")
                except Exception:
                    pass
                logger.exception('Compaction failed for %s batch %d', table_name, batch_num)

            current_start = current_end

        elapsed = time.time() - t_start
        self.stdout.write(self.style.SUCCESS(
            f'  {table_name}: compacted {total_compacted:,} rows in {batch_num} batches ({elapsed:.1f}s)'))
