import uuid
from django.db import models
from django.conf import settings


class RigTag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rig_tags')
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default='#6B7280')

    class Meta:
        unique_together = ('user', 'name')
        db_table = 'rigs_rigtag'

    def __str__(self):
        return self.name


class Rig(models.Model):
    class Status(models.TextChoices):
        ONLINE = 'online', 'Online'
        STALE = 'stale', 'Stale'
        OFFLINE = 'offline', 'Offline'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rigs')
    name = models.CharField(max_length=255, default='Unnamed Rig')
    expected_gpus = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OFFLINE)
    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    tags = models.ManyToManyField(RigTag, blank=True, related_name='rigs')

    class Meta:
        db_table = 'rigs_rig'

    def __str__(self):
        return f"{self.name} ({self.uuid})"

    def update_status(self):
        from django.utils import timezone
        from datetime import timedelta
        if self.last_seen is None:
            self.status = self.Status.OFFLINE
        elif timezone.now() - self.last_seen > timedelta(minutes=10):
            self.status = self.Status.OFFLINE
        elif timezone.now() - self.last_seen > timedelta(minutes=2):
            self.status = self.Status.STALE
        else:
            self.status = self.Status.ONLINE
        self.save(update_fields=['status'])
