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

    # Error count for this snapshot (integer, aggregated for error frequency charts)
    error_count = models.PositiveIntegerField(default=0)

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
    gpu_core_clock_mhz = models.PositiveIntegerField(null=True)
    gpu_mem_clock_mhz = models.PositiveIntegerField(null=True)

    class Meta:
        db_table = 'metrics_gpumetric'
        unique_together = ('rig_uuid', 'timestamp', 'gpu_index')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
            # Composite index for fleet overview batched GPU query
            # Supports: DISTINCT ON (rig_uuid, gpu_index) ORDER BY rig_uuid, gpu_index, -timestamp
            models.Index(fields=['rig_uuid', 'gpu_index', '-timestamp'],
                         name='gpumetric_rig_gpu_ts_idx'),
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
    """Per-container time-series metrics — one row per container per heartbeat.

    Stores only the fields needed for historical charts:
    cpu_pct, mem_usage_bytes.
    Grouped by (rig_uuid, name) for chart display.
    """
    id = models.BigAutoField(primary_key=True)
    rig_uuid = models.UUIDField(db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    container_id = models.CharField(max_length=64, blank=True, default='')
    name = models.CharField(max_length=255, blank=True, default='')
    cpu_pct = models.FloatField(null=True)
    mem_usage_bytes = models.BigIntegerField(null=True)

    class Meta:
        db_table = 'metrics_dockercontainermetric'
        unique_together = ('rig_uuid', 'timestamp', 'name')
        indexes = [
            models.Index(fields=['rig_uuid', '-timestamp']),
        ]


class LatestDockerContainer(models.Model):
    """Latest Docker container snapshot per rig — for Live Metrics display.

    Stores the latest payload fields not needed for charts:
    image, status, uptime_s, restart_count, mem_limit_bytes.
    Delete-before-insert pattern: all rows for a rig are deleted
    before inserting the latest snapshot.
    """
    id = models.BigAutoField(primary_key=True)
    rig_uuid = models.UUIDField(db_index=True)
    container_id = models.CharField(max_length=64, blank=True, default='')
    name = models.CharField(max_length=255, blank=True, default='')
    image = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=32, blank=True, default='')
    uptime_s = models.PositiveIntegerField(null=True)
    restart_count = models.PositiveIntegerField(default=0)
    mem_limit_bytes = models.BigIntegerField(null=True)

    class Meta:
        db_table = 'metrics_latest_docker_container'
        unique_together = ('rig_uuid', 'name')
        indexes = [
            models.Index(fields=['rig_uuid']),
        ]


class LatestSnapshot(models.Model):
    """Denormalized latest snapshot per rig for fast dashboard loading.

    Stores the latest metric values from each heartbeat. GPU data is stored
    as JSON arrays (one entry per GPU) to support variable GPU counts.
    This enables the Fleet Overview to load from a single row per rig
    without querying the GPUMetric timeseries table.
    """
    rig_uuid = models.UUIDField(primary_key=True)
    schema_version = models.CharField(max_length=10, default='1.0')
    timestamp = models.DateTimeField()
    cpu_utilization_pct = models.FloatField(null=True)
    cpu_temp_c = models.FloatField(null=True)
    mem_used_bytes = models.BigIntegerField(null=True)
    mem_total_bytes = models.BigIntegerField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    # GPU data stored as JSON arrays for fast dashboard access
    # Each array has one entry per GPU, ordered by gpu_index
    gpu_count = models.PositiveSmallIntegerField(default=0)
    gpu_models_json = models.JSONField(default=list, blank=True)       # ["RTX 3060", "RTX 3060"]
    gpu_temps_json = models.JSONField(default=list, blank=True)         # [72.5, 73.1]
    gpu_utils_json = models.JSONField(default=list, blank=True)         # [98.0, 100.0]
    gpu_fans_json = models.JSONField(default=list, blank=True)          # [74, 76]
    gpu_core_clocks_json = models.JSONField(default=list, blank=True)   # [2100, 2100]
    gpu_mem_clocks_json = models.JSONField(default=list, blank=True)    # [8000, 8000]
    gpu_mem_used_json = models.JSONField(default=list, blank=True)      # [8192, 8192]
    gpu_mem_total_json = models.JSONField(default=list, blank=True)     # [12288, 12288]
    gpu_mem_util_pcts_json = models.JSONField(default=list, blank=True)  # [66.7, 66.7]
    gpu_mem_free_json = models.JSONField(default=list, blank=True)       # [4096, 4096]
    gpu_power_draws_json = models.JSONField(default=list, blank=True)   # [350.5, 340.2]
    gpu_power_limits_json = models.JSONField(default=list, blank=True)  # [450, 450]
    gpu_pcie_gen_json = models.JSONField(default=list, blank=True)       # [4, 4]
    gpu_pcie_max_gen_json = models.JSONField(default=list, blank=True)   # [4, 4]
    gpu_pcie_width_json = models.JSONField(default=list, blank=True)     # [16, 16]
    gpu_pcie_max_width_json = models.JSONField(default=list, blank=True) # [16, 16]

    # Storage data stored as JSON arrays for fast dashboard access
    # Each array has one entry per disk device, ordered by device name
    storage_count = models.PositiveSmallIntegerField(default=0)
    storage_devices_json = models.JSONField(default=list, blank=True)       # ["/dev/sda", "/dev/sdb"]
    storage_fstypes_json = models.JSONField(default=list, blank=True)        # ["ext4", "xfs"]
    storage_mountpoints_json = models.JSONField(default=list, blank=True)    # ["/", "/home"]
    storage_capacities_json = models.JSONField(default=list, blank=True)     # [500107862016, 1000204886016]
    storage_usage_pcts_json = models.JSONField(default=list, blank=True)     # [72.5, 45.2]
    storage_temps_json = models.JSONField(default=list, blank=True)          # [35, 40]
    storage_smart_json = models.JSONField(default=list, blank=True)          # ["OK", "OK"]

    # Network data stored as JSON arrays for fast dashboard access
    # Each array has one entry per network interface, ordered by interface name
    network_count = models.PositiveSmallIntegerField(default=0)
    network_interfaces_json = models.JSONField(default=list, blank=True)     # ["eth0", "wlan0"]
    network_ipv4s_json = models.JSONField(default=list, blank=True)          # ["192.168.1.10", "10.0.0.5"]
    network_speeds_json = models.JSONField(default=list, blank=True)         # [1000, 300]
    network_rx_bytes_json = models.JSONField(default=list, blank=True)       # [1234567890, 987654321]
    network_tx_bytes_json = models.JSONField(default=list, blank=True)       # [567890123, 123456789]
    network_rx_errors_json = models.JSONField(default=list, blank=True)      # [0, 2]
    network_tx_errors_json = models.JSONField(default=list, blank=True)      # [0, 1]

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





