#!/usr/bin/env python3
"""
GPU Rig Monitoring Platform — Daily Database Maintenance Script

Runs in sequence:
1. compact_data — aggregate 1-minute rows into 1-hour buckets (data > 1 day old)
2. cleanup_old_data — delete data older than retention period (default: 31 days)
3. vacuum_analyze — reclaim dead tuples and update planner statistics

Usage:
  python manage.py daily_maintenance
  python manage.py daily_maintenance --dry-run
  python manage.py daily_maintenance --days 14
  python manage.py daily_maintenance --verbose
"""

import logging
import subprocess
import sys
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

# Tables to VACUUM ANALYZE after maintenance (in FK-safe order)
VACUUM_TABLES = [
    'metrics_gpumetric',
    'metrics_storagemetric',
    'metrics_networkmetric',
    'metrics_gpu_process',
    'metrics_power_reading',
    'metrics_metricsnapshot',
]


class Command(BaseCommand):
    help = 'Daily database maintenance: compact + cleanup + vacuum analyze'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=31,
                            help='Retention period in days (default: 31)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview without making changes')
        parser.add_argument('--verbose', action='store_true',
                            help='Show detailed statistics')
        parser.add_argument('--skip-compact', action='store_true',
                            help='Skip compaction step')
        parser.add_argument('--skip-cleanup', action='store_true',
                            help='Skip cleanup step')
        parser.add_argument('--skip-vacuum', action='store_true',
                            help='Skip vacuum analyze step')

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        verbose = options['verbose']
        skip_compact = options['skip_compact']
        skip_cleanup = options['skip_cleanup']
        skip_vacuum = options['skip_vacuum']

        start_time = timezone.now()
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Daily Database Maintenance — {start_time.strftime("%Y-%m-%d %H:%M:%S")}'))
        self.stdout.write(f'  Retention: {days} days')
        self.stdout.write(f'  Dry run: {dry_run}')
        self.stdout.write('')

        # Show pre-maintenance stats
        if verbose:
            self._show_table_stats('BEFORE')

        # Step 1: Compact data
        if not skip_compact:
            self.stdout.write(self.style.MIGRATE_HEADING('Step 1: Compacting data...'))
            try:
                call_command('compact_data', dry_run=dry_run, verbose=verbose)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  Compaction failed: {e}'))
                logger.exception('Compaction failed')
        else:
            self.stdout.write(self.style.WARNING('Step 1: SKIPPED (compact_data)'))

        self.stdout.write('')

        # Step 2: Cleanup old data
        if not skip_cleanup:
            self.stdout.write(self.style.MIGRATE_HEADING('Step 2: Cleaning up old data...'))
            try:
                call_command('cleanup_old_data', days=days, dry_run=dry_run, verbose=verbose)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  Cleanup failed: {e}'))
                logger.exception('Cleanup failed')
        else:
            self.stdout.write(self.style.WARNING('Step 2: SKIPPED (cleanup_old_data)'))

        self.stdout.write('')

        # Step 3: VACUUM ANALYZE
        if not skip_vacuum:
            self.stdout.write(self.style.MIGRATE_HEADING('Step 3: VACUUM ANALYZE...'))
            self._run_vacuum_analyze(dry_run, verbose)
        else:
            self.stdout.write(self.style.WARNING('Step 3: SKIPPED (vacuum_analyze)'))

        self.stdout.write('')

        # Show post-maintenance stats
        if verbose:
            self._show_table_stats('AFTER')

        elapsed = (timezone.now() - start_time).total_seconds()
        self.stdout.write(self.style.SUCCESS(
            f'Maintenance complete in {elapsed:.1f}s'))

    def _run_vacuum_analyze(self, dry_run, verbose):
        """Run VACUUM ANALYZE on affected tables."""
        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUN — skipping VACUUM ANALYZE'))
            return

        for table_name in VACUUM_TABLES:
            try:
                # VACUUM ANALYZE cannot run inside a transaction block
                # Use autocommit mode
                with connection.cursor() as cursor:
                    # Set autocommit for VACUUM
                    old_isolation = connection.connection.isolation_level
                    connection.connection.set_isolation_level(0)  # AUTOCOMMIT
                    try:
                        cursor.execute(f'VACUUM ANALYZE {table_name}')
                        if verbose:
                            self.stdout.write(f'  {table_name}: VACUUM ANALYZE complete')
                    finally:
                        connection.connection.set_isolation_level(old_isolation)
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'  {table_name}: VACUUM ANALYZE failed — {e}'))
                logger.exception('VACUUM ANALYZE failed for %s', table_name)

        self.stdout.write(self.style.SUCCESS('  VACUUM ANALYZE complete'))

    def _show_table_stats(self, label):
        """Show table statistics for monitoring."""
        self.stdout.write(self.style.MIGRATE_HEADING(f'Table Stats ({label}):'))
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT relname, n_live_tup, n_dead_tup,
                           ROUND(pg_total_relation_size(oid) / 1024.0 / 1024.0, 1) AS total_mb,
                           last_vacuum, last_autovacuum
                    FROM pg_stat_user_tables
                    WHERE schemaname = 'public'
                      AND relname IN %s
                    ORDER BY pg_total_relation_size(oid) DESC
                """, [tuple(VACUUM_TABLES)])
                rows = cursor.fetchall()
                for row in rows:
                    name, live, dead, mb, last_vac, last_auto = row
                    dead_info = f', dead: {dead:,}' if dead > 0 else ''
                    self.stdout.write(
                        f'  {name}: {live:,} rows, {mb} MB{dead_info}')
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  Could not fetch stats: {e}'))
