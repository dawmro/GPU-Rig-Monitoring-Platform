from django.contrib import admin
from .models import MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, DockerContainerMetric, LatestSnapshot, ErrorEvent, RigHardware


@admin.register(RigHardware)
class RigHardwareAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'cpu_model', 'cpu_physical_cores', 'cpu_logical_cores', 'mobo_manufacturer', 'mobo_model', 'updated_at')
    list_filter = ('mobo_manufacturer',)
    search_fields = ('rig_uuid', 'cpu_model', 'mobo_model')


@admin.register(MetricSnapshot)
class MetricSnapshotAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'cpu_utilization_pct', 'cpu_temp_c', 'mem_used_bytes', 'agent_version')
    list_filter = ('schema_version',)
    search_fields = ('rig_uuid',)


@admin.register(GPUMetric)
class GPUMetricAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'gpu_index', 'gpu_util_pct', 'gpu_temp_c', 'mem_used_mb', 'power_draw_w')
    list_filter = ('gpu_index',)
    search_fields = ('rig_uuid',)


@admin.register(StorageMetric)
class StorageMetricAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'device', 'mountpoint', 'usage_pct', 'temp_c')
    search_fields = ('rig_uuid', 'device')


@admin.register(NetworkMetric)
class NetworkMetricAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'interface', 'ipv4', 'link_speed_mbps', 'rx_bytes', 'tx_bytes')
    search_fields = ('rig_uuid', 'interface', 'ipv4')


@admin.register(DockerContainerMetric)
class DockerContainerMetricAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'name', 'image', 'status', 'restart_count')
    search_fields = ('rig_uuid', 'name')


@admin.register(ErrorEvent)
class ErrorEventAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'source', 'message', 'count', 'last_seen')
    list_filter = ('source',)
    search_fields = ('rig_uuid', 'message')
