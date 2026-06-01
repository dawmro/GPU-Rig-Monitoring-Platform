import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=50, db_index=True)
    target_type = models.CharField(max_length=50, blank=True, default='')
    target_id = models.CharField(max_length=255, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'audit_auditlog'
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.timestamp} {self.action} by {self.user}"
