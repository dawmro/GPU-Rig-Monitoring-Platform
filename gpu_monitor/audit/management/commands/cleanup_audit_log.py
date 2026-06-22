from django.core.management.base import BaseCommand
from audit.models import AuditLog
from datetime import timedelta
from django.utils import timezone


class Command(BaseCommand):
    help = 'Clean up old audit log entries'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90, help='Retention period in days')
        parser.add_argument('--dry-run', action='store_true', help='Preview without deleting')

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options['days'])
        old_logs = AuditLog.objects.filter(timestamp__lt=cutoff)
        count = old_logs.count()

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN: Would delete {count} audit log entries older than {options["days"]} days'))
            return

        if count == 0:
            self.stdout.write('No old audit log entries to clean up.')
            return

        old_logs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {count} audit log entries older than {options["days"]} days'))
