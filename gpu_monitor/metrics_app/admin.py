from django.contrib import admin
from .models import MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, DockerContainerMetric, LatestSnapshot, ErrorEvent, ErrorEventOccurrence, RigStatusEvent, AIProcessMetric


@admin.register(MetricSnapshot)
class MetricSnapshotAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'cpu_model', 'cpu_utilization_pct', 'cpu_temp_c', 'mem_used_bytes', 'agent_version')
    list_filter = ('schema_version',)
    search_fields = ('rig_uuid',)


@admin.register(GPUMetric)
class GPUMetricAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'gpu_index', 'model', 'gpu_uuid', 'gpu_util_pct', 'gpu_temp_c', 'mem_used_mb', 'power_draw_w')
    list_filter = ('gpu_index',)
    search_fields = ('rig_uuid', 'gpu_uuid', 'model')


@admin.register(StorageMetric)
class StorageMetricAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'device', 'mountpoint', 'capacity_bytes', 'usage_pct', 'temp_c')
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


@admin.register(ErrorEventOccurrence)
class ErrorEventOccurrenceAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'error_event', 'timestamp')
    list_filter = ('error_event__source',)
    search_fields = ('rig_uuid',)
    readonly_fields = ('timestamp',)


@admin.register(RigStatusEvent)
class RigStatusEventAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'status', 'previous_status')
    list_filter = ('status',)
    search_fields = ('rig_uuid',)
    readonly_fields = ('timestamp',)


@admin.register(AIProcessMetric)
class AIProcessMetricAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'process_name', 'pid', 'gpu_uuid', 'gpu_mem_used_mb', 'cpu_pct')
    list_filter = ('process_name',)
    search_fields = ('rig_uuid', 'process_name', 'gpu_uuid')
