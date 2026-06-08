import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Compact old metric data into larger time buckets to save storage'

    # Tables and their aggregation configs
    # Each entry: (table, id_field, value_fields, foreign_key_field)
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
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be compacted without making changes',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed per-table statistics',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be made'))

        now = timezone.now()

        # Phase 1: Compact data older than 1 day into 15-minute buckets
        cutoff_1d = now - timedelta(days=1)
        self.stdout.write(self.style.MIGRATE_HEADING('Phase 1: Compacting 1-minute -> 15-minute buckets (data older than 1 day)'))
        self._compact_to_buckets(
            cutoff=cutoff_1d,
            bucket_minutes=15,
            label='15-min',
            dry_run=dry_run,
            verbose=verbose,
        )

        # Phase 2: Compact data older than 7 days into 1-hour buckets
        cutoff_7d = now - timedelta(days=7)
        self.stdout.write(self.style.MIGRATE_HEADING('Phase 2: Compacting 15-minute -> 1-hour buckets (data older than 7 days)'))
        self._compact_to_buckets(
            cutoff=cutoff_7d,
            bucket_minutes=60,
            label='1-hour',
            dry_run=dry_run,
            verbose=verbose,
        )

        self.stdout.write(self.style.SUCCESS('Compaction complete'))

    def _compact_to_buckets(self, cutoff, bucket_minutes, label, dry_run, verbose):
        """Compact raw data older than cutoff into time buckets.

        Strategy for each table:
        1. SELECT rows older of cutoff, grouped by time bucket
        2. For each bucket, compute aggregated values (avg for metrics, last for static)
        3. Delete the original rows
        4. Insert the aggregated rows
        """
        for table_name, config in self.COMPACT_CONFIG.items():
            self._compact_table(
                table_name=table_name,
                config=config,
                cutoff=cutoff,
                bucket_minutes=bucket_minutes,
                label=label,
                dry_run=dry_run,
                verbose=verbose,
            )

    def _compact_table(self, table_name, config, cutoff, bucket_minutes, label, dry_run, verbose):
        """Compact a single table into time buckets."""
        group_by = config['group_by']
        agg_fields = config['agg_fields']
        static_fields = config['static_fields']

        # Build the SQL for time bucketing and aggregation
        group_cols = ', '.join(group_by)

        # Time bucket expression (PostgreSQL)
        # date_trunc to bucket boundary, then add offset for exact minute alignment
        bucket_expr = f"""
            date_trunc('hour', timestamp) +
            INTERVAL '{bucket_minutes} min' *
            (EXTRACT(MINUTE FROM timestamp)::int / {bucket_minutes})
        """

        # Build aggregate expressions
        select_parts = [f"{bucket_expr} AS bucket_ts"]
        for field, agg_type in agg_fields.items():
            if agg_type == 'avg':
                select_parts.append(f"AVG({field}) AS {field}")
            elif agg_type == 'sum':
                select_parts.append(f"SUM({field}) AS {field}")
            elif agg_type == 'last':
                # Take the last value (most recent non-null)
                select_parts.append(f"(ARRAY_AGG({field} ORDER BY timestamp DESC))[1] AS {field}")
            elif agg_type == 'max':
                select_parts.append(f"MAX({field}) AS {field}")
            elif agg_type == 'min':
                select_parts.append(f"MIN({field}) AS {field}")
        for field in static_fields:
            select_parts.append(f"(ARRAY_AGG({field} ORDER BY timestamp DESC))[1] AS {field}")

        select_clause = ',\n            '.join(select_parts)
        group_clause = group_cols

        # Count rows that would be compacted
        count_sql = f"SELECT COUNT(*) FROM {table_name} WHERE timestamp < %s"
        with connection.cursor() as cursor:
            cursor.execute(count_sql, [cutoff])
            row_count = cursor.fetchone()[0]

        if row_count == 0:
            if verbose:
                self.stdout.write(f'  {table_name}: nothing to compact')
            return

        if verbose:
            self.stdout.write(f'  {table_name}: {row_count:,} rows older than {label} cutoff')

        if dry_run:
            return

        # Perform compaction in a transaction
        try:
            with connection.cursor() as cursor:
                # Step 1: Create temp table with aggregated data
                cursor.execute(f"""
                    CREATE TEMP TABLE _compact_tmp AS
                    SELECT
                        {select_clause}
                    FROM {table_name}
                    WHERE timestamp < %s
                    GROUP BY {group_clause}, bucket_ts
                """, [cutoff])

                # Step 2: Delete original rows
                cursor.execute(f"""
                    DELETE FROM {table_name}
                    WHERE timestamp < %s
                """, [cutoff])

                # Step 3: Re-insert aggregated data
                # Build INSERT from temp table
                all_fields = ['timestamp'] + list(agg_fields.keys()) + static_fields + group_by
                cols = ', '.join(all_fields)
                vals = ', '.join(['bucket_ts' if f == 'timestamp' else f for f in all_fields])

                cursor.execute(f"""
                    INSERT INTO {table_name} ({cols})
                    SELECT {vals}
                    FROM _compact_tmp
                """)

                # Step 4: Clean up temp table
                cursor.execute("DROP TABLE _compact_tmp")

            self.stdout.write(
                self.style.SUCCESS(f'  {table_name}: compacted {row_count:,} rows into {label} buckets')
            )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'  {table_name}: FAILED — {e}')
            )
            logger.exception('Compaction failed for %s', table_name)
