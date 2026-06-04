import hashlib
import logging
from rest_framework import serializers, status
from django.db import transaction
from django.utils import timezone
from .models import MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, DockerContainerMetric, LatestSnapshot, ErrorEvent, RigHardware
from audit.middleware import compute_error_hash

logger = logging.getLogger(__name__)


class IngestSerializer(serializers.Serializer):
    rig_uuid = serializers.UUIDField()
    rig_name = serializers.CharField(required=False, default='')
    schema_version = serializers.CharField(default='1.0')
    agent_version = serializers.CharField(default='1.0.0')
    timestamp = serializers.DateTimeField()
    static = serializers.JSONField(required=False, default=dict)
    metrics = serializers.JSONField(required=False, default=dict)
    software = serializers.JSONField(required=False, default=dict)
    errors = serializers.ListField(required=False, default=list)

    def validate_schema_version(self, value):
        if value not in ('1.0',):
            raise serializers.ValidationError(f"Unsupported schema_version: {value}")
        return value


def process_ingest(rig_uuid, data, owner_id):
    """Process an ingestion payload. Returns (response_data, status_code)."""
    serializer = IngestSerializer(data=data)
    if not serializer.is_valid():
        return {'status': 'error', 'message': serializer.errors}, status.HTTP_400_BAD_REQUEST

    validated = serializer.validated_data
    rig_uuid = str(validated['rig_uuid'])
    ts = validated['timestamp']
    schema_version = validated['schema_version']
    static_data = validated.get('static', {})
    metrics_data = validated.get('metrics', {})
    software_data = validated.get('software', {})
    errors_data = validated.get('errors', [])

    cpu_static = static_data.get('cpu', {})
    gpu_static = static_data.get('gpus', [])
    mobo_static = static_data.get('motherboard', {})

    cpu_metrics = metrics_data.get('cpu', {})
    memory_metrics = metrics_data.get('memory', {})
    gpu_list = metrics_data.get('gpus', [])
    storage_list = metrics_data.get('storage', [])
    network_list = metrics_data.get('network', [])
    docker_containers = metrics_data.get('docker_containers', [])

    try:
        with transaction.atomic():
            # Upsert metric snapshot with idempotency
            snapshot, created = MetricSnapshot.objects.update_or_create(
                rig_uuid=rig_uuid,
                schema_version=schema_version,
                timestamp=ts,
                defaults={
                    'agent_version': validated.get('agent_version', '1.0.0'),
                    'cpu_utilization_pct': cpu_metrics.get('utilization_pct'),
                    'cpu_temp_c': cpu_metrics.get('temp_c'),
                    'mem_used_bytes': memory_metrics.get('used_bytes'),
                    'mem_cached_bytes': memory_metrics.get('cached_bytes'),
                    'inventory_json': static_data,
                    'software_json': software_data,
                },
            )

            # Upsert static hardware info (only updates when values change)
            RigHardware.objects.update_or_create(
                rig_uuid=rig_uuid,
                defaults={
                    'cpu_model': cpu_static.get('model', ''),
                    'cpu_physical_cores': cpu_static.get('physical_cores'),
                    'cpu_logical_cores': cpu_static.get('logical_cores'),
                    'mobo_manufacturer': mobo_static.get('manufacturer', ''),
                    'mobo_model': mobo_static.get('model', ''),
                    'bios_version': mobo_static.get('bios_version', ''),
                    'gpu_static_json': gpu_static,
                },
            )

            # Store per-GPU time-series metrics
            for gpu in gpu_list:
                GPUMetric.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    gpu_index=gpu.get('gpu_index', 0),
                    defaults={
                        'snapshot': snapshot,
                        'gpu_util_pct': gpu.get('gpu_util_pct'),
                        'gpu_temp_c': gpu.get('temp_c'),
                        'fan_speed_pct': gpu.get('fan_speed_pct'),
                        'mem_used_mb': gpu.get('mem_used_mb'),
                        'mem_util_pct': gpu.get('mem_util_pct'),
                        'power_draw_w': gpu.get('power_draw_w'),
                        'power_limit_w': gpu.get('power_limit_w'),
                    },
                )

            # Store per-disk metrics
            for disk in storage_list:
                StorageMetric.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    device=disk.get('device', ''),
                    defaults={
                        'snapshot': snapshot,
                        'mountpoint': disk.get('mountpoint', ''),
                        'fstype': disk.get('fstype', ''),
                        'usage_pct': disk.get('usage_pct'),
                        'temp_c': disk.get('temp_c'),
                        'smart_health': disk.get('smart_health', ''),
                    },
                )

            # Store per-interface metrics
            for iface in network_list:
                NetworkMetric.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    interface=iface.get('interface', ''),
                    defaults={
                        'snapshot': snapshot,
                        'ipv4': iface.get('ipv4', ''),
                        'link_speed_mbps': iface.get('link_speed_mbps'),
                        'rx_bytes': iface.get('rx_bytes'),
                        'tx_bytes': iface.get('tx_bytes'),
                        'rx_errors': iface.get('rx_errors'),
                        'tx_errors': iface.get('tx_errors'),
                    },
                )

            # Store per-container metrics
            for container in docker_containers:
                DockerContainerMetric.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    name=container.get('name', ''),
                    defaults={
                        'snapshot': snapshot,
                        'image': container.get('image', ''),
                        'status': container.get('status', ''),
                        'restart_count': container.get('restart_count', 0),
                    },
                )

            # Update latest snapshot (denormalized)
            LatestSnapshot.objects.update_or_create(
                rig_uuid=rig_uuid,
                defaults={
                    'schema_version': schema_version,
                    'timestamp': ts,
                    'cpu_utilization_pct': cpu_metrics.get('utilization_pct'),
                    'cpu_temp_c': cpu_metrics.get('temp_c'),
                    'mem_used_bytes': memory_metrics.get('used_bytes'),
                    'mem_total_bytes': memory_metrics.get('total_bytes'),
                },
            )

            # Process errors (deduplicate)
            for error in errors_data:
                source = error.get('source', '')
                message = error.get('message', '')
                error_hash = compute_error_hash(source, message)
                ErrorEvent.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    hash=error_hash,
                    defaults={
                        'timestamp': ts,
                        'source': source,
                        'message': message[:500],
                    },
                )

            http_status = status.HTTP_200_OK if created else status.HTTP_202_ACCEPTED
            status_label = 'new' if created else 'duplicate'

            return {
                'status': status_label,
                'message': f'Payload {status_label}',
                'next_expected': '',
            }, http_status

    except Exception as e:
        logger.exception("Ingestion failed for rig %s", rig_uuid)
        return {
            'status': 'error',
            'message': f'Internal error: {str(e)}',
        }, status.HTTP_500_INTERNAL_SERVER_ERROR
