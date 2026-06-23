from django.db import models
from django.conf import settings
from django.utils import timezone


class MetricSnapshot(models.Model):
    """Time-series metric data — one row per rig per minute.

    Stores dynamic metrics from the agent payload for historical chart
    aggregation. Static fields (cpu_model, mem_total, motherboard, software)
    are stored in LatestSnapshot for fast dashboard display.
    """
    id = models.BigAutoField(primary_key=True)
    rig_uuid = models.UUIDField(db_index=True)
    schema_version = models.CharField(max_length=10, default='1.0')
    timestamp = models.DateTimeField(db_index=True)

    # CPU metrics (dynamic — change every heartbeat, used for charts)
    cpu_utilization_pct = models.FloatField(null=True)
    cpu_temp_c = models.FloatField(null=True)
    cpu_load_avg_json = models.JSONField(default=list, blank=True)
    cpu_freq_current_mhz = models.FloatField(null=True, blank=True)
    cpu_freq_min_mhz = models.FloatField(null=True, blank=True)
    cpu_freq_max_mhz = models.FloatField(null=True, blank=True)

    # Memory metrics (dynamic — used for charts)
    mem_total_bytes = models.BigIntegerField(null=True)
    mem_used_bytes = models.BigIntegerField(null=True)
    mem_free_bytes = models.BigIntegerField(null=True)
    mem_cached_bytes = models.BigIntegerField(null=True)
    swap_used_bytes = models.BigIntegerField(null=True)
    swap_total_bytes = models.BigIntegerField(null=True)

    # Rig status at time of this snapshot (online/offline/stale)
    status = models.CharField(max_length=10, null=True, blank=True)

    # Uptime in seconds (dynamic — increases over time, used for uptime chart)
    uptime_s = models.PositiveIntegerField(null=True)

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
    Disk I/O metrics: throughput (bytes), IOPS (operations), and utilization (%).
    Throughput and IOPS are stored as cumulative counters; deltas are computed
    during ingest by comparing with the previous reading for the same device.
    Utilization is derived from busy_time delta / sample interval.
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

    # Disk I/O metrics — cumulative counters (like network rx/tx_bytes)
    read_bytes = models.BigIntegerField(null=True, help_text="Cumulative bytes read (counter)")
    write_bytes = models.BigIntegerField(null=True, help_text="Cumulative bytes written (counter)")
    # Deltas computed during ingest (bytes/sec equivalent over sample interval)
    read_bytes_delta = models.BigIntegerField(null=True, help_text="Bytes read since last sample")
    write_bytes_delta = models.BigIntegerField(null=True, help_text="Bytes written since last sample")
    # IOPS — cumulative operation counters
    read_iops = models.PositiveIntegerField(null=True, help_text="Cumulative read operations (counter)")
    write_iops = models.PositiveIntegerField(null=True, help_text="Cumulative write operations (counter)")
    # IOPS deltas computed during ingest
    read_iops_delta = models.PositiveIntegerField(null=True, help_text="Read operations since last sample")
    write_iops_delta = models.PositiveIntegerField(null=True, help_text="Write operations since last sample")
    # Busy time — cumulative ms the disk spent doing I/O
    busy_time_ms = models.PositiveIntegerField(null=True, help_text="Cumulative busy time in ms (counter)")
    # Utilization — derived: busy_time_delta / (sample_interval_s * 1000) * 100
    utilization_pct = models.FloatField(null=True, help_text="Disk utilization % (0-100)")

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


class LatestDockerContainer(models.Model):
    """Latest Docker container snapshot per rig — for Live Metrics display.

    Stores the latest payload fields for container status display.
    Delete-before-insert pattern: all rows for a rig are deleted
    before inserting the latest snapshot.
    """
    id = models.BigAutoField(primary_key=True)
    rig_uuid = models.UUIDField(db_index=True)
    container_id = models.CharField(max_length=64, blank=True, default='')
    name = models.CharField(max_length=255, blank=True, default='')
    image = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=32, blank=True, default='')
    created = models.CharField(max_length=64, blank=True, default='')
    status_text = models.CharField(max_length=255, blank=True, default='')

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
    updated_at = models.DateTimeField(auto_now=True)

    # CPU metrics (dynamic — change every heartbeat)
    cpu_utilization_pct = models.FloatField(null=True)
    cpu_temp_c = models.FloatField(null=True)
    cpu_load_avg_json = models.JSONField(default=list, blank=True)

    # CPU frequency (dynamic — updated every heartbeat)
    cpu_freq_current_mhz = models.FloatField(null=True, blank=True)
    cpu_freq_min_mhz = models.FloatField(null=True, blank=True)
    cpu_freq_max_mhz = models.FloatField(null=True, blank=True)

    # CPU info (static — can change on CPU swap, updated in-place)
    cpu_model = models.CharField(max_length=255, blank=True, default='')
    cpu_physical_cores = models.PositiveIntegerField(null=True)
    cpu_logical_cores = models.PositiveIntegerField(null=True)

    # Memory metrics (dynamic)
    mem_used_bytes = models.BigIntegerField(null=True)
    mem_free_bytes = models.BigIntegerField(null=True)
    mem_cached_bytes = models.BigIntegerField(null=True)
    swap_used_bytes = models.BigIntegerField(null=True)
    # Memory info (static — can change on RAM upgrade, updated in-place)
    mem_total_bytes = models.BigIntegerField(null=True)
    swap_total_bytes = models.BigIntegerField(null=True)

    # Motherboard info (static — can change on mobo swap, updated in-place)
    motherboard_json = models.JSONField(default=dict, blank=True)

    # Software info (static/semi-static — updated in-place on change)
    # Contains: hostname, os_distro, kernel, uptime_s, nvidia_driver, docker_version
    software_json = models.JSONField(default=dict, blank=True)
    agent_version = models.CharField(max_length=20, blank=True, default='')

    # GPU data stored as JSON arrays for fast dashboard access
    # Each array has one entry per GPU, ordered by gpu_index
    gpu_count = models.PositiveSmallIntegerField(default=0)
    gpu_uuids_json = models.JSONField(default=list, blank=True)         # ["GPU-abc-123", "GPU-def-456"]
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
    # Disk I/O metrics — latest deltas for Live Metrics display
    storage_read_bytes_delta_json = models.JSONField(default=list, blank=True)   # [12345678, 9876543] bytes/s
    storage_write_bytes_delta_json = models.JSONField(default=list, blank=True)  # [5678901, 1234567] bytes/s
    storage_read_iops_delta_json = models.JSONField(default=list, blank=True)    # [150, 80] IOPS
    storage_write_iops_delta_json = models.JSONField(default=list, blank=True)   # [200, 45] IOPS
    storage_utilization_pcts_json = models.JSONField(default=list, blank=True)   # [45.2, 12.1] %
    # Disk I/O metrics — cumulative totals since boot (for Total Read/Write display)
    storage_read_bytes_total_json = models.JSONField(default=list, blank=True)   # [37688539648, 1614605331456] bytes
    storage_write_bytes_total_json = models.JSONField(default=list, blank=True)  # [156538570752, 1016877289472] bytes
    storage_read_iops_total_json = models.JSONField(default=list, blank=True)    # [3309393, 43692476] operations
    storage_write_iops_total_json = models.JSONField(default=list, blank=True)   # [6397960, 15305417] operations

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

    # Top processes (latest snapshot only — for Live Metrics display)
    # Each entry: [{pid, name, cpu_pct, mem_pct, username, num_threads, cmdline}, ...]
    top_cpu_processes_json = models.JSONField(default=list, blank=True)     # Top 20 by CPU%
    top_mem_processes_json = models.JSONField(default=list, blank=True)     # Top 20 by memory%
    process_count = models.PositiveIntegerField(default=0)                   # Total running processes

    # Power consumption (latest values — for Live Metrics display)
    power_gpu_w = models.FloatField(null=True, blank=True)      # Sum of all GPU power draws
    power_cpu_w = models.FloatField(null=True, blank=True)      # CPU power (RAPL or estimated)
    power_cpu_source = models.CharField(max_length=10, blank=True, default='')  # 'rapl' or 'estimate'
    power_other_w = models.FloatField(null=True, blank=True)    # Flat 50W for RAM+disks+MB+fans
    power_total_dc_w = models.FloatField(null=True, blank=True) # Total DC power
    power_total_ac_w = models.FloatField(null=True, blank=True) # Total AC power (after PSU efficiency)
    power_cost_per_hour = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)  # $/hr

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


class PowerReading(models.Model):
    """Power consumption reading — one row per rig per heartbeat.

    Stores measured (GPU via nvidia-smi, CPU via RAPL) and estimated
    (CPU fallback, other components) power consumption data.
    Used for power charts and cost estimation.
    """
    id = models.BigAutoField(primary_key=True)
    rig = models.ForeignKey('rigs.Rig', on_delete=models.CASCADE, related_name='power_readings')
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    # GPU power (measured via nvidia-smi, sum of all GPUs)
    gpu_power_w = models.FloatField(default=0)

    # CPU power (measured via RAPL or estimated from utilization)
    cpu_power_w = models.FloatField(default=0)
    cpu_power_source = models.CharField(max_length=10, default='rapl', choices=[
        ('rapl', 'RAPL (measured)'),
        ('estimate', 'Estimated from utilization'),
    ])
    cpu_utilization = models.FloatField(default=0)
    cpu_cores = models.PositiveSmallIntegerField(default=0)

    # Other components (flat estimate: RAM + disks + MB + fans)
    other_power_w = models.FloatField(default=50)

    # Totals
    total_dc_power_w = models.FloatField(default=0)  # Sum of all components (DC)
    total_ac_power_w = models.FloatField(default=0)  # After PSU efficiency (AC)
    psu_efficiency = models.FloatField(default=0.9)  # Applied PSU efficiency

    class Meta:
        db_table = 'metrics_power_reading'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['rig', '-timestamp']),
        ]





