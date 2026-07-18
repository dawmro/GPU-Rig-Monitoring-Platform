"""
Celery tasks for rigs app.

Migrates the rig status update logic from management command to Celery task.
"""
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from rigs.models import Rig
from metrics_app.models import RigStatusEvent


@shared_task(bind=True, queue='maintenance', priority=5)
def update_rig_status(self):
    """
    Update rig status (stale/offline) based on last_seen timestamp.
    
    Runs every 2 minutes via Celery Beat.
    Migrated from management command `update_rig_status`.
    
    Returns:
        dict: {'stale': int, 'offline': int, 'processed': int}
    """
    now = timezone.now()
    stale_threshold = now - timedelta(minutes=2)
    offline_threshold = now - timedelta(minutes=10)

    stale_count = 0
    offline_count = 0

    # Mark rigs as stale if not seen in 2-10 minutes
    stale_rigs = Rig.objects.filter(
        status=Rig.Status.ONLINE,
        last_seen__lt=stale_threshold,
        last_seen__gte=offline_threshold,
    )
    for rig in stale_rigs:
        rig.status = Rig.Status.STALE
        rig.save(update_fields=['status'])
        RigStatusEvent.objects.create(
            rig_uuid=str(rig.uuid),
            status=Rig.Status.STALE,
            previous_status=Rig.Status.ONLINE,
        )
        stale_count += 1

    # Mark rigs as offline if not seen in 10+ minutes
    offline_rigs = Rig.objects.filter(
        last_seen__lt=offline_threshold,
    ).exclude(status=Rig.Status.OFFLINE)

    for rig in offline_rigs:
        old_status = rig.status
        rig.status = Rig.Status.OFFLINE
        rig.save(update_fields=['status'])
        RigStatusEvent.objects.create(
            rig_uuid=str(rig.uuid),
            status=Rig.Status.OFFLINE,
            previous_status=old_status,
        )
        offline_count += 1

    return {
        'stale': stale_count,
        'offline': offline_count,
        'processed': stale_count + offline_count,
        'timestamp': timezone.now().isoformat(),
    }
