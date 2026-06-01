import logging
from django.utils import timezone
from datetime import timedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import SimpleRateThrottle
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404

from accounts.authentication import APIKeyAuthentication
from accounts.models import ApiKey
from .serializers import process_ingest
from .models import LatestSnapshot, MetricSnapshot, ErrorEvent
from rigs.models import Rig
from audit.middleware import log_audit_event

logger = logging.getLogger(__name__)


class IngestRateThrottle(SimpleRateThrottle):
    scope = 'ingest'

    def get_cache_key(self, request, view):
        key = request.headers.get('X-API-Key', '')
        return f'ingest_{key[:16]}'


@method_decorator(csrf_exempt, name='dispatch')
class IngestView(APIView):
    """POST /api/v1/ingest/ — Accept telemetry payload from agents."""
    authentication_classes = [APIKeyAuthentication]
    throttle_classes = [IngestRateThrottle]

    def post(self, request):
        user = request.user
        api_key = request.auth
        data = request.data

        if not isinstance(data, dict):
            return Response({'status': 'error', 'message': 'Expected JSON object'}, status=400)

        rig_uuid = str(data.get('rig_uuid', ''))
        if not rig_uuid:
            return Response({'status': 'error', 'message': 'Missing rig_uuid'}, status=400)

        # Check ownership
        try:
            rig = Rig.objects.get(uuid=rig_uuid)
        except Rig.DoesNotExist:
            # Auto-create rig on first seen
            rig = Rig.objects.create(
                uuid=rig_uuid,
                owner=user,
                expected_gpus=0,
            )
            log_audit_event(request, 'rig.enrolled', 'Rig', rig.uuid,
                          {'agent_version': data.get('agent_version', ''), 'ip': request.META.get('REMOTE_ADDR')})
        else:
            if rig.owner_id != user.id:
                return Response({'status': 'error', 'message': 'UUID already claimed by another user'}, status=409)

        # Process the payload
        result, http_status = process_ingest(rig_uuid, data, user.id)

        # Update rig last_seen and status
        rig.last_seen = timezone.now()
        rig.status = Rig.Status.ONLINE
        rig.save(update_fields=['last_seen', 'status'])

        return Response(result, status=http_status)


class HealthView(APIView):
    """GET /api/v1/health/ — Internal health check."""
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        try:
            from django.db import connections
            conn = connections['default']
            conn.ensure_connection()
            db_status = 'ok'
        except Exception:
            db_status = 'error'

        active_rigs = Rig.objects.filter(
            last_seen__gte=timezone.now() - timedelta(minutes=2)
        ).count()

        return Response({
            'status': 'healthy',
            'version': '1.0.0',
            'uptime_s': 0,
            'db_connection': db_status,
            'active_rigs': active_rigs,
        })


class RigMetricsView(APIView):
    """GET /api/v1/rigs/<uuid>/metrics/ — Latest metrics for a rig."""

    def get(self, request, uuid):
        user = request.user
        rig = get_object_or_404(Rig, uuid=uuid)
        if rig.owner_id != user.id and not user.is_staff:
            return Response({'status': 'error', 'message': 'Forbidden'}, status=403)

        try:
            snapshot = LatestSnapshot.objects.get(rig_uuid=str(uuid))
            data = {
                'rig_uuid': str(uuid),
                'timestamp': snapshot.timestamp.isoformat() if snapshot.timestamp else None,
                'cpu_utilization_pct': snapshot.cpu_utilization_pct,
                'cpu_temp_c': snapshot.cpu_temp_c,
                'mem_used_bytes': snapshot.mem_used_bytes,
                'mem_total_bytes': snapshot.mem_total_bytes,
                'gpu_metrics': snapshot.gpu_metrics_json,
                'storage': snapshot.storage_json,
                'network': snapshot.network_json,
                'docker_containers': snapshot.docker_containers_json,
                'software': snapshot.software_json,
                'errors': snapshot.errors_json,
            }
        except LatestSnapshot.DoesNotExist:
            data = {'rig_uuid': str(uuid), 'timestamp': None}

        return Response(data)


class ChartDataView(APIView):
    """GET /api/v1/rigs/<uuid>/chart-data/ — Historical chart data."""

    def get(self, request, uuid):
        user = request.user
        rig = get_object_or_404(Rig, uuid=uuid)
        if rig.owner_id != user.id and not user.is_staff:
            return Response({'status': 'error', 'message': 'Forbidden'}, status=403)

        metric = request.query_params.get('metric', 'cpu_utilization_pct')
        range_hours = int(request.query_params.get('range', '24'))

        since = timezone.now() - timedelta(hours=range_hours)

        snapshots = MetricSnapshot.objects.filter(
            rig_uuid=str(uuid),
            timestamp__gte=since,
        ).order_by('timestamp')[:2000]

        labels = []
        values = []
        for s in snapshots:
            labels.append(s.timestamp.isoformat())
            val = getattr(s, metric, None)
            values.append(val)

        return Response({
            'labels': labels,
            'datasets': [{'label': metric, 'data': values}],
        })
