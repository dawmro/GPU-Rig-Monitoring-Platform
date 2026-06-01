import hashlib
import logging
from rest_framework import serializers, status
from django.db import connection
from django.utils import timezone
from .models import MetricSnapshot, LatestSnapshot, ErrorEvent
from audit.middleware import compute_error_hash

logger = logging.getLogger(__name__)


class IngestSerializer(serializers.Serializer):
    rig_uuid = serializers.UUIDField()
    schema_version = serializers.CharField(default='1.0')
    agent_version = serializers.CharField(default='1.0.0')
    timestamp = serializers.DateTimeField()
    inventory = serializers.JSONField(required=False, default=dict)
    metrics = serializers.JSONField(required=False, default=dict)
    software = serializers.JSONField(required=False, default=dict)
    errors = serializers.ListField(required=False, default=list)

    def validate_schema_version(self, value):
        if value not in ('1.0',):
            raise serializers.ValidationError(f"Unsupported schema_version: {value}")
        return value


def process_ingest(rig_uuid, data, owner_id):
    """Process an ingestion payload. Returns (response_data, status_code)."""
    from django.db import transaction

    serializer = IngestSerializer(data=data)
    if not serializer.is_valid():
        return {'status': 'error', 'message': serializer.errors}, status.HTTP_400_BAD_REQUEST

    validated = serializer.validated_data
    rig_uuid = str(validated['rig_uuid'])
    ts = validated['timestamp']
    schema_version = validated['schema_version']
    metrics_data = validated.get('metrics', {})
    software_data = validated.get('software', {})
    inventory_data = validated.get('inventory', {})
    errors_data = validated.get('errors', [])

    cpu = metrics_data.get('cpu', {})
    memory = metrics_data.get('memory', {})
    gpu_list = metrics_data.get('gpus', [])
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
                    'mem_total_bytes': memory.get('total_bytes'),
                    'mem_used_bytes': memory.get('used_bytes'),
                    'mem_cached_bytes': memory.get('cached_bytes'),
                    'storage_json': storage_list,
                    'network_json': network_list,
                    'gpu_metrics_json': gpu_list,
                    'ai_processes_json': ai_processes,
                    'docker_containers_json': docker_containers,
                    'software_json': software_data,
                    'errors_json': errors_data,
                    'inventory_json': inventory_data,
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
                    'gpu_metrics_json': gpu_list,
                    'storage_json': storage_list,
                    'network_json': network_list,
                    'docker_containers_json': docker_containers,
                    'software_json': software_data,
                    'errors_json': errors_data,
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
