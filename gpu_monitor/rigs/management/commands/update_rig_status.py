from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from rigs.models import Rig
from metrics_app.models import RigStatusEvent


class Command(BaseCommand):
    help = 'Update rig status (stale/offline) based on last_seen timestamp'

    def _transition(self, rig, new_status):
        """Update rig status and log the transition."""
        old_status = rig.status
        if old_status != new_status:
            rig.status = new_status
            rig.save(update_fields=['status'])
            RigStatusEvent.objects.create(
                rig_uuid=str(rig.uuid),
                status=new_status,
                previous_status=old_status,
            )
            # Invalidate cached rig data — status just changed; without this
            # the cached status would be stale for up to 30s after transition.
            from dashboard.views import invalidate_rig_cache
            invalidate_rig_cache(rig.uuid)
            return 1
        return 0

    def handle(self, *args, **options):
        now = timezone.now()

        # Mark rigs as stale if not seen in 2-10 minutes
        stale_threshold = now - timedelta(minutes=2)
        offline_threshold = now - timedelta(minutes=10)

        stale_count = 0
        for rig in Rig.objects.filter(
            status=Rig.Status.ONLINE,
            last_seen__lt=stale_threshold,
            last_seen__gte=offline_threshold,
        ):
            stale_count += self._transition(rig, Rig.Status.STALE)

        offline_count = 0
        for rig in Rig.objects.filter(
            last_seen__lt=offline_threshold,
        ).exclude(status=Rig.Status.OFFLINE):
            offline_count += self._transition(rig, Rig.Status.OFFLINE)

        self.stdout.write(
            self.style.SUCCESS(
                f'Updated: {stale_count} stale, {offline_count} offline'
            )
        )