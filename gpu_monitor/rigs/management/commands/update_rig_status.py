from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from rigs.models import Rig


class Command(BaseCommand):
    help = 'Update rig status (stale/offline) based on last_seen timestamp'

    def handle(self, *args, **options):
        now = timezone.now()

        # Mark rigs as stale if not seen in 2-10 minutes
        stale_threshold = now - timedelta(minutes=2)
        offline_threshold = now - timedelta(minutes=10)

        stale_count = Rig.objects.filter(
            status=Rig.Status.ONLINE,
            last_seen__lt=stale_threshold,
            last_seen__gte=offline_threshold,
        ).update(status=Rig.Status.STALE)

        offline_count = Rig.objects.filter(
            last_seen__lt=offline_threshold,
        ).update(status=Rig.Status.OFFLINE)

        # Also mark rigs that were stale but now offline
        offline_from_stale = Rig.objects.filter(
            status=Rig.Status.STALE,
            last_seen__lt=offline_threshold,
        ).update(status=Rig.Status.OFFLINE)

        self.stdout.write(
            self.style.SUCCESS(
                f'Updated: {stale_count} stale, {offline_count + offline_from_stale} offline'
            )
        )
