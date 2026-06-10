import logging
import os
import sys
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection

logger = logging.getLogger(__name__)

# Tables in dependency order — parent FIRST, then children
# Parent is compacted first so its CASCADE delete removes old child rows.
# Then children are compacted independently.
COMPACT_TABLES = [
    # Parent table FIRST (compacts and cascades to children)
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
        },
        'static_fields': [
            'cpu_model', 'cpu_physical_cores', 'cpu_logical_cores',
            'mem_total_bytes', 'schema_version', 'agent_version',
            'software_json', 'motherboard_json',
        ],
    },
    # Child tables (compacted after parent, with their own timestamps)
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
    {
        'table': 'metrics_gpu_process',
        'group_by': ['rig_uuid', 'gpu_index', 'pid'],
        'agg_fields': {
            'gpu_mem_mb': 'avg',
        },
        'static_fields': ['name', 'type', 'snapshot_id'],
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
            self.stdout.write(self.style.WARNING('DRY RUN -- no changes will be made'))

        now = timezone.now()

        # Phase 1: Compact data older than 1 day into 1-hour buckets
        cutoff_1d = now - timedelta(days=1)
        self.stdout.write(self.style.MIGRATE_HEADING(
            'Phase 1: Compacting 1-minute -> 1-hour buckets (data older than 1 day)'))
        self._compact_phase(cutoff_1d, 60, '1-hour', dry_run, verbose)

        self.stdout.write(self.style.SUCCESS('Compaction complete'))

    def _compact_phase(self, cutoff, bucket_minutes, label, dry_run, verbose):
        """Compact all tables for a given phase.

        Strategy:
        1. Compact parent table (metrics_metricsnapshot) FIRST.
           Its CASCADE delete removes old child rows automatically.
        2. Compact child tables SECOND.
           Old child rows are already gone (cascaded from parent).
           Only compact remaining rows (if any).
        """
        for config in COMPACT_TABLES:
            table_name = config['table']
            self._compact_table(table_name, config, cutoff, bucket_minutes, label, dry_run, verbose)

    def _compact_table(self, table_name, config, cutoff, bucket_minutes, label, dry_run, verbose):
        """Compact a single table into time buckets."""
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

        # For parent table, only compact rows NOT referenced by child tables
        # to avoid FK violations
        if table_name == 'metrics_metricsnapshot':
            where_sql = """
                timestamp < %s
                AND id NOT IN (
                    SELECT DISTINCT snapshot_id FROM metrics_gpumetric WHERE timestamp < %s AND snapshot_id IS NOT NULL
                    UNION
                    SELECT DISTINCT snapshot_id FROM metrics_storagemetric WHERE timestamp < %s AND snapshot_id IS NOT NULL
                    UNION
                    SELECT DISTINCT snapshot_id FROM metrics_networkmetric WHERE timestamp < %s AND snapshot_id IS NOT NULL
                    UNION
                    SELECT DISTINCT snapshot_id FROM metrics_dockercontainermetric WHERE timestamp < %s AND snapshot_id IS NOT NULL
                    UNION
                    SELECT DISTINCT snapshot_id FROM metrics_ai_process WHERE timestamp < %s AND snapshot_id IS NOT NULL
                    UNION
                    SELECT DISTINCT snapshot_id FROM metrics_gpu_process WHERE timestamp < %s AND snapshot_id IS NOT NULL
                )
            """
            params = [cutoff] * 7  # 1 for main WHERE + 6 for UNION subqueries
        else:
            where_sql = "timestamp < %s"
            params = [cutoff]

        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM " + table_name + " WHERE " + where_sql, params)
            row_count = cursor.fetchone()[0]

        if row_count == 0:
            if verbose:
                self.stdout.write(f'  {table_name}: nothing to compact')
            return

        if verbose:
            self.stdout.write(f'  {table_name}: {row_count:,} rows older than {label} cutoff')

        if dry_run:
            return

        tmp_table = f"_compact_tmp_{table_name.replace('.', '_')}"

        try:
            with connection.cursor() as cursor:
                cursor.execute("DROP TABLE IF EXISTS " + tmp_table)
                cursor.execute(
                    "CREATE TEMP TABLE " + tmp_table + " AS "
                    "SELECT " + select_clause + " "
                    "FROM " + table_name + " "
                    "WHERE " + where_sql + " "
                    "GROUP BY " + group_cols + ", bucket_ts",
                    params
                )

                cursor.execute("DELETE FROM " + table_name + " WHERE " + where_sql, params)

                insert_fields = ['timestamp'] + list(agg_fields.keys()) + static_fields + group_by
                cols = ', '.join(insert_fields)
                vals = ', '.join(['bucket_ts' if f == 'timestamp' else f for f in insert_fields])

                cursor.execute("INSERT INTO " + table_name + " (" + cols + ") SELECT " + vals + " FROM " + tmp_table)
                cursor.execute("DROP TABLE IF EXISTS " + tmp_table)

            self.stdout.write(self.style.SUCCESS(
                f'  {table_name}: compacted {row_count:,} rows into {label} buckets'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  {table_name}: FAILED -- {e}'))
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f"DROP TABLE IF EXISTS {tmp_table}")
            except Exception:
                pass
            logger.exception('Compaction failed for %s', table_name)

