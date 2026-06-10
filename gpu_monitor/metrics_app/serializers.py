import logging
from rest_framework import serializers, status
from django.db import transaction
from django.utils import timezone
from .models import MetricSnapshot, GPUMetric, GPUProcessMetric, StorageMetric, NetworkMetric, DockerContainerMetric, LatestSnapshot, RigStatusEvent, AIProcessMetric
from rigs.models import Rig

logger = logging.getLogger(__name__)


class IngestSerializer(serializers.Serializer):
    rig_uuid = serializers.UUIDField()
    rig_name = serializers.CharField(required=False, default='')
    schema_version = serializers.CharField(default='1.1')
    agent_version = serializers.CharField(default='1.1.0')
    timestamp = serializers.DateTimeField()
    metrics = serializers.JSONField(required=False, default=dict)
    motherboard = serializers.JSONField(required=False, default=dict)
    software = serializers.JSONField(required=False, default=dict)
    errors = serializers.ListField(required=False, default=list)

    def validate_schema_version(self, value):
        if value not in ('1.0', '1.1', '1.2', '1.3'):
            raise serializers.ValidationError(f"Unsupported schema_version: {value}")
        return value


def process_ingest(rig_uuid, data, owner_id, rig=None):
    """Process an ingestion payload. Returns (response_data, status_code)."""
    serializer = IngestSerializer(data=data)
    if not serializer.is_valid():
        return {'status': 'error', 'message': serializer.errors}, status.HTTP_400_BAD_REQUEST

    validated = serializer.validated_data
    rig_uuid = str(validated['rig_uuid'])
    ts = validated['timestamp']
    schema_version = validated['schema_version']
    metrics_data = validated.get('metrics', {})
    motherboard_data = validated.get('motherboard', {})
    software_data = validated.get('software', {})
    errors_data = validated.get('errors', [])

    cpu = metrics_data.get('cpu', {})
    memory = metrics_data.get('memory', {})
    gpu_list = metrics_data.get('gpus', [])
    gpu_process_list = metrics_data.get('gpu_processes', [])
    storage_list = metrics_data.get('storage', [])
    network_list = metrics_data.get('network', [])
    ai_processes = metrics_data.get('ai_processes', [])
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
                    'cpu_model': cpu.get('model', ''),
                    'cpu_utilization_pct': cpu.get('utilization_pct'),
                    'cpu_temp_c': cpu.get('temp_c'),
                    'cpu_physical_cores': cpu.get('physical_cores'),
                    'cpu_logical_cores': cpu.get('logical_cores'),
                    'cpu_load_avg_json': cpu.get('load_avg', []),
                    'mem_total_bytes': memory.get('total_bytes'),
                    'mem_used_bytes': memory.get('used_bytes'),
                    'mem_free_bytes': memory.get('free_bytes'),
                    'mem_cached_bytes': memory.get('cached_bytes'),
                    'swap_used_bytes': memory.get('swap_used_bytes'),
                    'swap_total_bytes': memory.get('swap_total_bytes'),
                    'status': rig.status if rig else None,
                    'motherboard_json': motherboard_data,
                    'software_json': software_data,
                    'error_count': len(errors_data),
                },
            )

            # Store per-GPU metrics (with uuid, model, mem_total — all per-row for tracking)
            for idx, gpu in enumerate(gpu_list):
                GPUMetric.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    gpu_index=idx,
                    defaults={
                        'snapshot': snapshot,
                        'gpu_uuid': gpu.get('uuid', ''),
                        'model': gpu.get('model', ''),
                        'gpu_util_pct': gpu.get('gpu_util_pct'),
                        'gpu_temp_c': gpu.get('temp_c'),
                        'fan_speed_pct': gpu.get('fan_speed_pct'),
                        'mem_total_mb': gpu.get('mem_total_mb'),
                        'mem_used_mb': gpu.get('mem_used_mb'),
                        'mem_free_mb': gpu.get('mem_free_mb'),
                        'mem_util_pct': gpu.get('mem_util_pct'),
                        'power_draw_w': gpu.get('power_draw_w'),
                        'power_limit_w': gpu.get('power_limit_w'),
                        'pcie_current_gen': gpu.get('pcie_current_gen'),
                        'pcie_max_gen': gpu.get('pcie_max_gen'),
                        'pcie_current_width': gpu.get('pcie_current_width'),
                        'pcie_max_width': gpu.get('pcie_max_width'),
                        'gpu_core_clock_mhz': gpu.get('gpu_core_clock_mhz'),
                        'gpu_mem_clock_mhz': gpu.get('gpu_mem_clock_mhz'),
                    },
                )

            # Store per-GPU process metrics
            # Delete old process records for this rig first — we only care about
            # the latest snapshot, not historical process data
            GPUProcessMetric.objects.filter(rig_uuid=rig_uuid).delete()
            for proc in gpu_process_list:
                GPUProcessMetric.objects.create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    snapshot=snapshot,
                    gpu_index=proc.get('gpu_index', 0),
                    pid=proc.get('pid'),
                    process_name=proc.get('name', '')[:500],
                    type=proc.get('type', ''),
                    gpu_mem_mb=proc.get('gpu_mem_mb'),
                )

            # Store per-disk metrics (with capacity — for tracking)
            for disk in storage_list:
                StorageMetric.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    device=disk.get('device', ''),
                    defaults={
                        'snapshot': snapshot,
                        'mountpoint': disk.get('mountpoint', ''),
                        'fstype': disk.get('fstype', ''),
                        'capacity_bytes': disk.get('capacity_bytes'),
                        'usage_pct': disk.get('usage_pct'),
                        'temp_c': disk.get('temp_c'),
                        'smart_health': disk.get('smart_health', ''),
                    },
                )

            # Store per-interface metrics with traffic delta calculation
            for iface in network_list:
                iface_name = iface.get('interface', '')
                new_rx = iface.get('rx_bytes')
                new_tx = iface.get('tx_bytes')

                # Calculate deltas by comparing with previous reading for this interface
                rx_delta = None
                tx_delta = None
                try:
                    prev = NetworkMetric.objects.filter(
                        rig_uuid=rig_uuid,
                        interface=iface_name,
                    ).order_by('-timestamp').first()
                    if prev and new_rx is not None and new_tx is not None:
                        rx_delta = new_rx - prev.rx_bytes if prev.rx_bytes else None
                        tx_delta = new_tx - prev.tx_bytes if prev.tx_bytes else None
                        # Handle counter wraparound (shouldn't happen with 64-bit counters, but be safe)
                        if rx_delta is not None and rx_delta < 0:
                            rx_delta = new_rx
                        if tx_delta is not None and tx_delta < 0:
                            tx_delta = new_tx
                except Exception:
                    pass

                NetworkMetric.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    interface=iface_name,
                    defaults={
                        'snapshot': snapshot,
                        'ipv4': iface.get('ipv4', ''),
                        'link_speed_mbps': iface.get('link_speed_mbps'),
                        'rx_bytes': new_rx,
                        'tx_bytes': new_tx,
                        'rx_bytes_delta': rx_delta,
                        'tx_bytes_delta': tx_delta,
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
                        'cpu_pct': container.get('cpu_pct'),
                        'mem_usage_bytes': container.get('mem_usage_bytes'),
                        'mem_limit_bytes': container.get('mem_limit_bytes'),
                    },
                )

            # Update latest snapshot (denormalized)
            LatestSnapshot.objects.update_or_create(
                rig_uuid=rig_uuid,
                defaults={
                    'schema_version': schema_version,
                    'timestamp': ts,
                    'cpu_utilization_pct': cpu.get('utilization_pct'),
                    'cpu_temp_c': cpu.get('temp_c'),
                    'mem_used_bytes': memory.get('used_bytes'),
                    'mem_total_bytes': memory.get('total_bytes'),
                },
            )

            # Store per-process AI metrics
            for proc in ai_processes:
                AIProcessMetric.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    process_name=proc.get('process_name', ''),
                    pid=proc.get('pid'),
                    defaults={
                        'snapshot': snapshot,
                        'gpu_uuid': proc.get('gpu_uuid', ''),
                        'gpu_mem_used_mb': proc.get('gpu_mem_used_mb'),
                        'cpu_pct': proc.get('cpu_pct'),
                    },
                )

            # Track rig status transitions
            if rig:
                previous_status = rig.status
                current_status = Rig.Status.ONLINE  # Heartbeat always means online
                if previous_status != current_status:
                    RigStatusEvent.objects.create(
                        rig_uuid=rig_uuid,
                        status=current_status,
                        previous_status=previous_status,
                    )

            # Update latest error text on Rig (like motherboard_json — updated in place)
            if errors_data and rig:
                rig.latest_errors_json = [
                    {'source': e.get('source', ''), 'message': e.get('message', '')[:200], 'timestamp': e.get('timestamp', '')}
                    for e in errors_data[:10]
                ]
                rig.save(update_fields=['latest_errors_json'])

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
