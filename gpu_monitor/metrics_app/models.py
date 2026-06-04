from django.db import models
from django.conf import settings


class RigHardware(models.Model):
    """Static hardware inventory per rig. Updated only when values change.

    Stored separately from MetricSnapshot to avoid duplicating
    unchanging data (cpu model, core count, motherboard, gpu model)
    in every heartbeat row.
    """
    rig_uuid = models.UUIDField(primary_key=True, db_index=True)

    # Static CPU info
    cpu_model = models.CharField(max_length=255, blank=True, default='')
    cpu_physical_cores = models.PositiveIntegerField(null=True)
    cpu_logical_cores = models.PositiveIntegerField(null=True)

    # Static motherboard info
    mobo_manufacturer = models.CharField(max_length=255, blank=True, default='')
    mobo_model = models.CharField(max_length=255, blank=True, default='')
    bios_version = models.CharField(max_length=255, blank=True, default='')

    # Static GPU info (stored as JSON array since a rig can have multiple GPUs)
    # Each entry: {uuid, model, mem_total_mb}
    gpu_static_json = models.JSONField(default=list, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'metrics_rig_hardware'


class MetricSnapshot(models.Model):
    """Time-series metric data — one row per rig per minute.

    Only stores dynamic metrics that change between heartbeats.
    Static hardware info (cpu model, core count, etc.) is stored
    in RigHardware and sent in the 'static' payload section.
    """
    id = models.BigAutoField(primary_key=True)
    rig_uuid = models.UUIDField(db_index=True)
    schema_version = models.CharField(max_length=10, default='1.0')
    agent_version = models.CharField(max_length=20, default='1.0.0')
    timestamp = models.DateTimeField(db_index=True)

    # CPU time-series metrics (no model/cores — those are in RigHardware)
    cpu_utilization_pct = models.FloatField(null=True)
    cpu_temp_c = models.FloatField(null=True)

    # Memory time-series metrics (no total_bytes — that's relatively static)
    mem_used_bytes = models.BigIntegerField(null=True)
    mem_cached_bytes = models.BigIntegerField(null=True)

    # Full inventory snapshot (static info, updated less frequently)
    inventory_json = models.JSONField(default=dict, blank=True)
    software_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'metrics_metricsnapshot'
        unique_together = ('rig_uuid', 'schema_version', 'timestamp')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
        ]


class GPUMetric(models.Model):
    """Per-GPU time-series metrics — one row per GPU per snapshot.

    Only stores dynamic GPU metrics. Static info (model, uuid, mem_total_mb)
    is stored in RigHardware.gpu_static_json.
    """
    id = models.BigAutoField(primary_key=True)
    snapshot = models.ForeignKey(MetricSnapshot, on_delete=models.CASCADE, related_name='gpu_metrics')
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    gpu_index = models.PositiveSmallIntegerField(default=0)

    gpu_util_pct = models.FloatField(null=True)
    gpu_temp_c = models.FloatField(null=True)
    fan_speed_pct = models.FloatField(null=True)
    mem_used_mb = models.PositiveIntegerField(null=True)
    mem_util_pct = models.FloatField(null=True)
    power_draw_w = models.FloatField(null=True)
    power_limit_w = models.FloatField(null=True)

    class Meta:
        db_table = 'metrics_gpumetric'
        unique_together = ('rig_uuid', 'timestamp', 'gpu_index')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
        ]


class StorageMetric(models.Model):
    """Per-disk time-series metrics — one row per disk per snapshot.

    Only stores dynamic storage metrics. Capacity is relatively static
    and not included here.
    """
    id = models.BigAutoField(primary_key=True)
    snapshot = models.ForeignKey(MetricSnapshot, on_delete=models.CASCADE, related_name='storage_metrics')
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    device = models.CharField(max_length=255, blank=True, default='')
    mountpoint = models.CharField(max_length=512, blank=True, default='')
    fstype = models.CharField(max_length=32, blank=True, default='')
    usage_pct = models.FloatField(null=True)
    temp_c = models.FloatField(null=True)
    smart_health = models.CharField(max_length=16, blank=True, default='')

    class Meta:
        db_table = 'metrics_storagemetric'
        unique_together = ('rig_uuid', 'timestamp', 'device')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
        ]


class NetworkMetric(models.Model):
    """Per-interface time-series metrics — one row per interface per snapshot."""
    id = models.BigAutoField(primary_key=True)
    snapshot = models.ForeignKey(MetricSnapshot, on_delete=models.CASCADE, related_name='network_metrics')
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    interface = models.CharField(max_length=64, blank=True, default='')
    ipv4 = models.CharField(max_length=15, blank=True, default='')
    link_speed_mbps = models.PositiveIntegerField(null=True)
    rx_bytes = models.BigIntegerField(null=True)
    tx_bytes = models.BigIntegerField(null=True)
    rx_errors = models.PositiveIntegerField(null=True)
    tx_errors = models.PositiveIntegerField(null=True)

    class Meta:
        db_table = 'metrics_networkmetric'
        unique_together = ('rig_uuid', 'timestamp', 'interface')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
        ]


class DockerContainerMetric(models.Model):
    """Per-container time-series metrics — one row per container per snapshot."""
    id = models.BigAutoField(primary_key=True)
    snapshot = models.ForeignKey(MetricSnapshot, on_delete=models.CASCADE, related_name='docker_metrics')
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    name = models.CharField(max_length=255, blank=True, default='')
    image = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=32, blank=True, default='')
    restart_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'metrics_dockercontainermetric'
        unique_together = ('rig_uuid', 'timestamp', 'name')
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
