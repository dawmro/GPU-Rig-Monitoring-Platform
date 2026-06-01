from django.db import models
from django.conf import settings


class MetricSnapshot(models.Model):
    """Time-series metric data stored in TimescaleDB hypertable."""
    id = models.BigAutoField(primary_key=True)
    rig_uuid = models.UUIDField(db_index=True)
    schema_version = models.CharField(max_length=10, default='1.0')
    agent_version = models.CharField(max_length=20, default='1.0.0')
    timestamp = models.DateTimeField(db_index=True)

    # CPU metrics
    cpu_model = models.CharField(max_length=255, blank=True, default='')
    cpu_utilization_pct = models.FloatField(null=True)
    cpu_temp_c = models.FloatField(null=True)
    cpu_physical_cores = models.PositiveIntegerField(null=True)
    cpu_logical_cores = models.PositiveIntegerField(null=True)

    # Memory metrics
    mem_total_bytes = models.BigIntegerField(null=True)
    mem_used_bytes = models.BigIntegerField(null=True)
    mem_cached_bytes = models.BigIntegerField(null=True)

    # Storage JSON (array of disk info)
    storage_json = models.JSONField(default=list, blank=True)

    # Network JSON (array of interface info)
    network_json = models.JSONField(default=list, blank=True)

    # GPU metrics JSON (array of GPU info)
    gpu_metrics_json = models.JSONField(default=list, blank=True)

    # AI process info
    ai_processes_json = models.JSONField(default=list, blank=True)

    # Docker containers
    docker_containers_json = models.JSONField(default=list, blank=True)

    # Software info
    software_json = models.JSONField(default=dict, blank=True)

    # Errors
    errors_json = models.JSONField(default=list, blank=True)

    # Full inventory snapshot (static info, updated less frequently)
    inventory_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'metrics_metricsnapshot'
        unique_together = ('rig_uuid', 'schema_version', 'timestamp')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
        ]


class LatestSnapshot(models.Model):
    """Denormalized latest snapshot per rig for fast dashboard loading."""
    rig_uuid = models.UUIDField(primary_key=True)
    schema_version = models.CharField(max_length=10, default='1.0')
    timestamp = models.DateTimeField()
    cpu_utilization_pct = models.FloatField(null=True)
    cpu_temp_c = models.FloatField(null=True)
    mem_used_bytes = models.BigIntegerField(null=True)
    mem_total_bytes = models.BigIntegerField(null=True)
    gpu_metrics_json = models.JSONField(default=list, blank=True)
    storage_json = models.JSONField(default=list, blank=True)
    network_json = models.JSONField(default=list, blank=True)
    docker_containers_json = models.JSONField(default=list, blank=True)
    software_json = models.JSONField(default=dict, blank=True)
    errors_json = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'metrics_latest_snapshot'


class ErrorEvent(models.Model):
    """Deduplicated error events."""
    id = models.BigAutoField(primary_key=True)
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField()
    source = models.CharField(max_length=50, blank=True, default='')
    message = models.TextField(blank=True, default='')
    hash = models.CharField(max_length=64, db_index=True)
    count = models.PositiveIntegerField(default=1)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'metrics_lasterrors'
        unique_together = ('rig_uuid', 'hash')
