from django.db import models
from django.conf import settings
from django.utils import timezone


class MetricSnapshot(models.Model):
    """Time-series metric data — one row per rig per minute.

    Stores all metrics from the agent payload. Fields that are technically
    static (cpu_model, mem_total_bytes, etc.) are stored per-row for
    simplicity and to track any changes over time (e.g., hardware upgrades).
    """
    id = models.BigAutoField(primary_key=True)
    rig_uuid = models.UUIDField(db_index=True)
    schema_version = models.CharField(max_length=10, default='1.0')
    agent_version = models.CharField(max_length=20, default='1.0.0')
    timestamp = models.DateTimeField(db_index=True)

    # CPU metrics (static + dynamic)
    cpu_model = models.CharField(max_length=255, blank=True, default='')
    cpu_utilization_pct = models.FloatField(null=True)
    cpu_temp_c = models.FloatField(null=True)
    cpu_physical_cores = models.PositiveIntegerField(null=True)
    cpu_logical_cores = models.PositiveIntegerField(null=True)
    cpu_load_avg_json = models.JSONField(default=list, blank=True)

    # Memory metrics (static + dynamic)
    mem_total_bytes = models.BigIntegerField(null=True)
    mem_used_bytes = models.BigIntegerField(null=True)
    mem_free_bytes = models.BigIntegerField(null=True)
    mem_cached_bytes = models.BigIntegerField(null=True)
    swap_used_bytes = models.BigIntegerField(null=True)
    swap_total_bytes = models.BigIntegerField(null=True)

    # Rig status at time of this snapshot (online/offline/stale)
    status = models.CharField(max_length=10, null=True, blank=True)

    # Motherboard info (static, stored as JSON for flexibility)
    motherboard_json = models.JSONField(default=dict, blank=True)

    # Software info (static, stored as JSON)
    # Contains: hostname, os_distro, kernel, uptime_s, nvidia_driver, docker_version
    software_json = models.JSONField(default=dict, blank=True)

    # Error tracking (latest payload only, not historical)
    error_count = models.PositiveIntegerField(default=0)
    error_json = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = 'metrics_metricsnapshot'
        unique_together = ('rig_uuid', 'schema_version', 'timestamp')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
        ]


class GPUMetric(models.Model):
    """Per-GPU time-series metrics — one row per GPU per snapshot.

    Includes both static identifiers (uuid, model, mem_total_mb) and
    dynamic metrics (utilization, temp, power). UUID is stored per-row
    so GPU replacements can be tracked accurately over time.
    """
    id = models.BigAutoField(primary_key=True)
    snapshot = models.ForeignKey(MetricSnapshot, on_delete=models.CASCADE, related_name='gpu_metrics')
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    gpu_index = models.PositiveSmallIntegerField(default=0)

    gpu_uuid = models.CharField(max_length=64, blank=True, default='')
    model = models.CharField(max_length=255, blank=True, default='')
    gpu_util_pct = models.FloatField(null=True)
    gpu_temp_c = models.FloatField(null=True)
    fan_speed_pct = models.FloatField(null=True)
    mem_total_mb = models.PositiveIntegerField(null=True)
    mem_used_mb = models.PositiveIntegerField(null=True)
    mem_free_mb = models.PositiveIntegerField(null=True)
    mem_util_pct = models.FloatField(null=True)
    power_draw_w = models.FloatField(null=True)
    power_limit_w = models.FloatField(null=True)
    pcie_current_gen = models.PositiveSmallIntegerField(null=True)
    pcie_max_gen = models.PositiveSmallIntegerField(null=True)
    pcie_current_width = models.PositiveSmallIntegerField(null=True)
    pcie_max_width = models.PositiveSmallIntegerField(null=True)

    class Meta:
        db_table = 'metrics_gpumetric'
        unique_together = ('rig_uuid', 'timestamp', 'gpu_index')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
        ]


class StorageMetric(models.Model):
    """Per-disk time-series metrics — one row per disk per snapshot.

    Includes capacity (static) and dynamic metrics (usage, temp, smart).
    """
    id = models.BigAutoField(primary_key=True)
    snapshot = models.ForeignKey(MetricSnapshot, on_delete=models.CASCADE, related_name='storage_metrics')
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    device = models.CharField(max_length=255, blank=True, default='')
    mountpoint = models.CharField(max_length=512, blank=True, default='')
    fstype = models.CharField(max_length=32, blank=True, default='')
    capacity_bytes = models.BigIntegerField(null=True)
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
    rx_bytes_delta = models.BigIntegerField(null=True, help_text="Bytes received since last reading")
    tx_bytes_delta = models.BigIntegerField(null=True, help_text="Bytes sent since last reading")
    rx_errors = models.PositiveIntegerField(null=True)
    tx_errors = models.PositiveIntegerField(null=True)

    class Meta:
        db_table = 'metrics_networkmetric'
        unique_together = ('rig_uuid', 'timestamp', 'interface')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
        ]


class DockerContainerMetric(models.Model):
    """Per-container time-series metrics — one row per container per snapshot.

    Stores container status and resource usage for historical tracking.
    """
    id = models.BigAutoField(primary_key=True)
    snapshot = models.ForeignKey(MetricSnapshot, on_delete=models.CASCADE, related_name='docker_metrics')
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    name = models.CharField(max_length=255, blank=True, default='')
    image = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=32, blank=True, default='')
    restart_count = models.PositiveIntegerField(default=0)
    cpu_pct = models.FloatField(null=True)
    mem_usage_bytes = models.BigIntegerField(null=True)
    mem_limit_bytes = models.BigIntegerField(null=True)

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


class RigStatusEvent(models.Model):
    """Tracks rig status transitions over time for uptime reporting.

    Created whenever the rig's status changes (online→stale, stale→offline,
    offline→online, etc.). Also created on every heartbeat to track uptime
    continuity. Enables historical availability charts and downtime analysis.
    """
    id = models.BigAutoField(primary_key=True)
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(max_length=10, db_index=True)
    previous_status = models.CharField(max_length=10, null=True, blank=True)

    class Meta:
        db_table = 'metrics_rig_status_event'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
            models.Index(fields=['rig_uuid', 'status']),
        ]


class AIProcessMetric(models.Model):
    """Per-process GPU/CPU usage tracking for AI workloads.

    Stores per-process resource usage when the agent collects AI process data.
    The `ai_processes` array in the agent payload contains processes that are
    actively using GPU resources (detected via nvidia-smi or similar).

    Enables charts showing:
    - Which processes are using GPU memory over time
    - Per-process GPU memory usage trends
    - CPU usage breakdown by AI process
    """
    id = models.BigAutoField(primary_key=True)
    snapshot = models.ForeignKey(MetricSnapshot, on_delete=models.CASCADE, related_name='ai_processes')
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField(db_index=True)

    process_name = models.CharField(max_length=255, blank=True, default='')
    pid = models.PositiveIntegerField(null=True)
    gpu_uuid = models.CharField(max_length=64, blank=True, default='')
    gpu_mem_used_mb = models.PositiveIntegerField(null=True)
    cpu_pct = models.FloatField(null=True)

    class Meta:
        db_table = 'metrics_ai_process'
        ordering = ['-gpu_mem_used_mb']
        unique_together = ('rig_uuid', 'timestamp', 'process_name')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
            models.Index(fields=['rig_uuid', 'process_name']),
        ]


class GPUProcessMetric(models.Model):
    """Per-GPU-process metrics — one row per process per GPU per snapshot.

    Collected from nvidia-smi process table. Enables the Live Metrics
    "GPU Processes" display showing which processes use each GPU.

    Fields:
        gpu_index: GPU device index (0, 1, 2, ...)
        pid: Process ID from OS
        process_name: Process executable path/name
        type: Process type — C (Compute), G (Graphics), C+G (Both)
        gpu_mem_mb: GPU memory used by this process (MB)
    """
    id = models.BigAutoField(primary_key=True)
    snapshot = models.ForeignKey(MetricSnapshot, on_delete=models.CASCADE, related_name='gpu_processes')
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField(db_index=True)

    gpu_index = models.PositiveSmallIntegerField(default=0)
    pid = models.PositiveIntegerField(null=True)
    process_name = models.CharField(max_length=500, blank=True, default='')
    type = models.CharField(max_length=10, blank=True, default='')  # C, G, C+G
    gpu_mem_mb = models.PositiveIntegerField(null=True)

    class Meta:
        db_table = 'metrics_gpu_process'
        ordering = ['-gpu_mem_mb']
        unique_together = ('rig_uuid', 'timestamp', 'gpu_index', 'pid')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
        ]


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
