import logging
import os
import sys
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Compact old metric data into larger time buckets to save storage'

    COMPACT_CONFIG = {
        'metrics_metricsnapshot': {
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
            'static_fields': ['cpu_model', 'cpu_physical_cores', 'cpu_logical_cores',
                              'mem_total_bytes', 'schema_version', 'agent_version'],
        },
        'metrics_gpumetric': {
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
            'static_fields': ['gpu_uuid', 'model'],
        },
        'metrics_storagemetric': {
            'group_by': ['rig_uuid', 'device'],
            'agg_fields': {
                'usage_pct': 'avg',
                'temp_c': 'avg',
                'capacity_bytes': 'last',
            },
            'static_fields': ['mountpoint', 'fstype', 'smart_health'],
        },
        'metrics_networkmetric': {
            'group_by': ['rig_uuid', 'interface'],
            'agg_fields': {
                'rx_bytes_delta': 'sum',
                'tx_bytes_delta': 'sum',
                'rx_errors': 'sum',
                'tx_errors': 'sum',
                'link_speed_mbps': 'last',
                'ipv4': 'last',
            },
            'static_fields': [],
        },
    }

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
            'Phase 1: Compacting 1-minute -> 15-minute buckets (data older than 1 day)'))
        self._compact_to_buckets(cutoff_1d, 15, '15-min', dry_run, verbose)

        cutoff_7d = now - timedelta(days=7)
        self.stdout.write(self.style.MIGRATE_HEADING(
            'Phase 2: Compacting 15-minute -> 1-hour buckets (data older than 7 days)'))
        self._compact_to_buckets(cutoff_7d, 60, '1-hour', dry_run, verbose)

        self.stdout.write(self.style.SUCCESS('Compaction complete'))

    def _compact_to_buckets(self, cutoff, bucket_minutes, label, dry_run, verbose):
        for table_name, config in self.COMPACT_CONFIG.items():
            self._compact_table(table_name, config, cutoff, bucket_minutes, label, dry_run, verbose)

    def _compact_table(self, table_name, config, cutoff, bucket_minutes, label, dry_run, verbose):
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

        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE timestamp < %s", [cutoff])
            row_count = cursor.fetchone()[0]

        if row_count == 0:
            if verbose:
                self.stdout.write(f'  {table_name}: nothing to compact')
            return

        if verbose:
            self.stdout.write(f'  {table_name}: {row_count:,} rows older than {label} cutoff')

        if dry_run:
            return

        try:
            with connection.cursor() as cursor:
                cursor.execute(f"""
                    CREATE TEMP TABLE _compact_tmp AS
                    SELECT {select_clause}
                    FROM {table_name}
                    WHERE timestamp < %s
                    GROUP BY {group_cols}, bucket_ts
                """, [cutoff])

                cursor.execute(f"DELETE FROM {table_name} WHERE timestamp < %s", [cutoff])

                all_fields = ['timestamp'] + list(agg_fields.keys()) + static_fields + group_by
                cols = ', '.join(all_fields)
                vals = ', '.join(['bucket_ts' if f == 'timestamp' else f for f in all_fields])

                cursor.execute(f"INSERT INTO {table_name} ({cols}) SELECT {vals} FROM _compact_tmp")
                cursor.execute("DROP TABLE _compact_tmp")

            self.stdout.write(self.style.SUCCESS(
                f'  {table_name}: compacted {row_count:,} rows into {label} buckets'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  {table_name}: FAILED — {e}'))
            logger.exception('Compaction failed for %s', table_name)
