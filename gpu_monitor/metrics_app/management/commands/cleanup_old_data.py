#!/usr/bin/env python3
"""
GPU Rig Monitoring Platform — Old Data Cleanup Script

Deletes metric data older than specified days (default: 31).
Processes in batches to avoid long locks.

Optimizations:
- Hardcoded table list (no information_schema queries)
- Progress reporting for large deletions
- FK-safe ordering (children first, parent last)
"""

import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection

logger = logging.getLogger(__name__)

# Tables in FK-safe order: children first, parent last
# All tables have 'id' as PK except metrics_latest_snapshot (rig_uuid)
CLEANUP_TABLES = [
    {'table': 'metrics_gpu_process',         'pk': 'id',         'has_ts': True},
    {'table': 'metrics_gpumetric',           'pk': 'id',         'has_ts': True},
    {'table': 'metrics_storagemetric',       'pk': 'id',         'has_ts': True},
    {'table': 'metrics_networkmetric',       'pk': 'id',         'has_ts': True},
    {'table': 'metrics_latest_docker_container', 'pk': 'id',     'has_ts': False},
    {'table': 'metrics_rig_status_event',    'pk': 'id',         'has_ts': True},
    {'table': 'metrics_metricsnapshot',     'pk': 'id',         'has_ts': True},
    {'table': 'metrics_latest_snapshot',     'pk': 'rig_uuid',   'has_ts': False},
]

BATCH_SIZE = 10000
PROGRESS_INTERVAL = 10  # Report every N batches


class Command(BaseCommand):
    help = 'Delete metric data older than specified days (default: 31)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=31,
                            help='Delete data older than this many days (default: 31)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview without making changes')
        parser.add_argument('--verbose', action='store_true',
                            help='Show detailed per-table statistics')

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        verbose = options['verbose']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be made'))

        cutoff = timezone.now() - timedelta(days=days)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Cleaning up data older than {days} days (before {cutoff.strftime("%Y-%m-%d %H:%M")})'))

        total_deleted = 0
        for config in CLEANUP_TABLES:
            deleted = self._cleanup_table(config, cutoff, dry_run, verbose)
            total_deleted += deleted

        self.stdout.write(self.style.SUCCESS(f'Total rows deleted: {total_deleted:,}'))

    def _cleanup_table(self, config, cutoff, dry_run, verbose):
        table_name = config['table']
        pk_column = config['pk']
        has_timestamp = config['has_ts']

        # For tables without timestamp, skip age-based cleanup
        if not has_timestamp:
            if verbose:
                self.stdout.write(f'  {table_name}: skipped (no timestamp column)')
            return 0

        # Count rows to delete
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE timestamp < %s", [cutoff])
            row_count = cursor.fetchone()[0]

        if row_count == 0:
            if verbose:
                self.stdout.write(f'  {table_name}: nothing to delete')
            return 0

        if verbose or dry_run:
            self.stdout.write(f'  {table_name}: {row_count:,} rows to delete')

        if dry_run:
            return row_count

        # Delete in batches with progress reporting
        total_deleted = 0
        batch_num = 0

        try:
            with connection.cursor() as cursor:
                while True:
                    cursor.execute(
                        f"DELETE FROM {table_name} WHERE {pk_column} IN ("
                        f"  SELECT {pk_column} FROM {table_name} WHERE timestamp < %s LIMIT %s)",
                        [cutoff, BATCH_SIZE])
                    deleted = cursor.rowcount
                    total_deleted += deleted
                    batch_num += 1

                    if verbose and batch_num % PROGRESS_INTERVAL == 0:
                        pct = (total_deleted / row_count * 100) if row_count > 0 else 100
                        self.stdout.write(
                            f'    batch {batch_num}: {total_deleted:,}/{row_count:,} ({pct:.1f}%)')

                    if deleted < BATCH_SIZE:
                        break

            self.stdout.write(self.style.SUCCESS(
                f'  {table_name}: deleted {total_deleted:,} rows in {batch_num} batches'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  {table_name}: FAILED — {e}'))
            logger.exception('Cleanup failed for %s', table_name)

        return total_deleted
