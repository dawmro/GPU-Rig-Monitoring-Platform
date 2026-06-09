import logging
import os
import sys
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection

logger = logging.getLogger(__name__)

CLEANUP_TABLES = [
    'metrics_error_event_occurrence',
    'metrics_gpu_process',
    'metrics_gpumetric',
    'metrics_storagemetric',
    'metrics_networkmetric',
    'metrics_dockercontainermetric',
    'metrics_ai_process',
    'metrics_rig_status_event',
    'metrics_metricsnapshot',
    'metrics_latest_snapshot',
    'metrics_lasterrors',
]


class Command(BaseCommand):
    help = 'Delete metric data older than specified days (default: 31)'

    BATCH_SIZE = 10000

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
        for table_name in CLEANUP_TABLES:
            deleted = self._cleanup_table(table_name, cutoff, dry_run, verbose)
            total_deleted += deleted

        self.stdout.write(self.style.SUCCESS(f'Total rows deleted: {total_deleted:,}'))

    def _cleanup_table(self, table_name, cutoff, dry_run, verbose):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = 'timestamp'",
                [table_name])
            has_timestamp = cursor.fetchone() is not None

        if not has_timestamp:
            if verbose:
                self.stdout.write(f'  {table_name}: skipped (no timestamp column)')
            return 0

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

        # Determine primary key column (most tables use 'id', LatestSnapshot uses 'rig_uuid')
        pk_column = 'rig_uuid' if table_name == 'metrics_latest_snapshot' else 'id'

        total_deleted = 0
        try:
            with connection.cursor() as cursor:
                while True:
                    cursor.execute(
                        f"DELETE FROM {table_name} WHERE {pk_column} IN ("
                        f"  SELECT {pk_column} FROM {table_name} WHERE timestamp < %s LIMIT %s)",
                        [cutoff, self.BATCH_SIZE])
                    deleted = cursor.rowcount
                    total_deleted += deleted
                    if deleted < self.BATCH_SIZE:
                        break

            self.stdout.write(self.style.SUCCESS(
                f'  {table_name}: deleted {total_deleted:,} rows'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  {table_name}: FAILED — {e}'))
            logger.exception('Cleanup failed for %s', table_name)

        return total_deleted
