from django.contrib import admin
from .models import MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, LatestSnapshot, RigStatusEvent


@admin.register(MetricSnapshot)
class MetricSnapshotAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'cpu_utilization_pct', 'cpu_temp_c',
                    'mem_used_bytes', 'swap_used_bytes', 'status', 'error_count', 'agent_version')
    list_filter = ('schema_version', 'status')
    search_fields = ('rig_uuid',)


@admin.register(GPUMetric)
class GPUMetricAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'gpu_index', 'gpu_util_pct', 'gpu_temp_c',
                    'mem_used_mb', 'power_draw_w')
    list_filter = ('gpu_index',)
    search_fields = ('rig_uuid',)


@admin.register(StorageMetric)
class StorageMetricAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'device', 'usage_pct', 'temp_c')
    search_fields = ('rig_uuid', 'device')


@admin.register(NetworkMetric)
class NetworkMetricAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'interface', 'rx_bytes_delta', 'tx_bytes_delta',
                    'rx_errors', 'tx_errors')
    search_fields = ('rig_uuid', 'interface')


@admin.register(LatestSnapshot)
class LatestSnapshotAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'cpu_utilization_pct', 'cpu_temp_c',
                    'mem_used_bytes', 'mem_total_bytes', 'gpu_count', 'storage_count',
                    'network_count')
    search_fields = ('rig_uuid',)


@admin.register(RigStatusEvent)
class RigStatusEventAdmin(admin.ModelAdmin):
    list_display = ('rig_uuid', 'timestamp', 'status', 'previous_status')
    list_filter = ('status',)
    search_fields = ('rig_uuid',)
    readonly_fields = ('timestamp',)
