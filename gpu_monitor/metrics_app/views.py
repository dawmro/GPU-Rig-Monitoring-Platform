import logging
from django.utils import timezone
from datetime import timedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.authentication import SessionAuthentication
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
        rig_name = data.get('rig_name', '').strip()
        try:
            rig = Rig.objects.get(uuid=rig_uuid)
        except Rig.DoesNotExist:
            # Auto-create rig on first seen — use agent-suggested name or default
            name = rig_name or 'Unnamed Rig'
            rig = Rig.objects.create(
                uuid=rig_uuid,
                owner=user,
                name=name[:128],
                expected_gpus=0,
            )
            log_audit_event(request, 'rig.enrolled', 'Rig', rig.uuid,
                          {'agent_version': data.get('agent_version', ''), 'ip': request.META.get('REMOTE_ADDR')})
        else:
            if rig.owner_id != user.id:
                return Response({'status': 'error', 'message': 'UUID already claimed by another user'}, status=409)
            # Note: rig_name from agent is intentionally NOT applied here.
            # After initial creation, the rig name is managed exclusively
            # via the dashboard rename API to prevent config.yaml from
            # overwriting user-set names on every heartbeat.

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
    authentication_classes = [SessionAuthentication]

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
    """GET /api/v1/rigs/<uuid>/chart-data/ — Historical chart data.

    Returns fixed 1-minute buckets for the requested range (default 24h = 1440 points).
    Empty buckets are zero-filled so offline periods are visible on the chart.

    Query parameters:
        metric: Field name to chart. Supported values:
            - cpu_utilization_pct, cpu_temp_c, mem_used_bytes (from MetricSnapshot)
            - gpu_temp_c, gpu_util_pct, gpu_mem_used_mb, gpu_power_w (from GPUMetric, gpu_index=0)
            - disk_usage_pct (from StorageMetric, first disk)
        range: Hours of historical data (default: 24)
    """
    authentication_classes = [SessionAuthentication]

    # Metrics stored directly on MetricSnapshot
    SNAPSHOT_METRICS = {'cpu_utilization_pct', 'cpu_temp_c', 'mem_used_bytes', 'mem_total_bytes'}
    # Metrics stored on GPUMetric (per-GPU)
    # Maps chart/query metric names to actual GPUMetric field names
    GPU_METRICS = {
        'gpu_temp_c': 'gpu_temp_c',
        'gpu_util_pct': 'gpu_util_pct',
        'gpu_mem_used_mb': 'mem_used_mb',
        'gpu_mem_total_mb': 'mem_total_mb',
        'gpu_power_w': 'power_draw_w',
        'gpu_power_limit_w': 'power_limit_w',
        'gpu_fan_pct': 'fan_speed_pct',
    }
    # Metrics stored on StorageMetric
    STORAGE_METRICS = {'disk_usage_pct'}

    def _build_buckets(self, range_hours):
        """Build fixed 1-minute bucket labels and empty value array.

        Returns (labels, values) where labels are 'HH:MM' strings and
        values are a list of zeros with length = range_hours * 60.
        Bucket 0 = oldest, bucket N-1 = newest (now).
        """
        now = timezone.now()
        # Truncate to the start of the current minute
        end_minute = now.replace(second=0, microsecond=0)
        total_minutes = range_hours * 60
        start_minute = end_minute - timedelta(minutes=total_minutes)

        labels = []
        for i in range(total_minutes):
            bucket_time = start_minute + timedelta(minutes=i)
            labels.append(bucket_time.strftime('%H:%M'))

        values = [None] * total_minutes
        return labels, values, start_minute, end_minute

    def _fill_buckets(self, labels, values, start_minute, queryset, field_name, value_key='timestamp'):
        """Fill bucket values from a queryset.

        Each row is placed into the bucket matching its truncated minute.
        If multiple rows fall in the same bucket, the last one wins.
        """
        total_minutes = len(labels)
        for row in queryset:
            ts = getattr(row, value_key)
            # Truncate to minute
            ts_minute = ts.replace(second=0, microsecond=0)
            # Calculate bucket index
            delta = ts_minute - start_minute
            idx = int(delta.total_seconds() // 60)
            if 0 <= idx < total_minutes:
                val = getattr(row, field_name, None)
                if val is not None:
                    values[idx] = val

    def get(self, request, uuid):
        user = request.user
        rig = get_object_or_404(Rig, uuid=uuid)
        if rig.owner_id != user.id and not user.is_staff:
            return Response({'status': 'error', 'message': 'Forbidden'}, status=403)

        metric = request.query_params.get('metric', 'cpu_utilization_pct')
        range_hours = int(request.query_params.get('range', '24'))
        gpu_index = int(request.query_params.get('gpu_index', 0))

        labels, values, start_minute, end_minute = self._build_buckets(range_hours)

        if metric in self.SNAPSHOT_METRICS:
            snapshots = MetricSnapshot.objects.filter(
                rig_uuid=str(uuid),
                timestamp__gte=start_minute,
                timestamp__lte=end_minute,
            ).order_by('timestamp')[:10000]
            self._fill_buckets(labels, values, start_minute, snapshots, metric)

        elif metric in self.GPU_METRICS:
            from .models import GPUMetric
            gpu_data = GPUMetric.objects.filter(
                rig_uuid=str(uuid),
                gpu_index=gpu_index,
                timestamp__gte=start_minute,
                timestamp__lte=end_minute,
            ).order_by('timestamp')[:10000]
            field_name = self.GPU_METRICS[metric]
            self._fill_buckets(labels, values, start_minute, gpu_data, field_name)

        elif metric in self.STORAGE_METRICS:
            from .models import StorageMetric
            storage_data = StorageMetric.objects.filter(
                rig_uuid=str(uuid),
                timestamp__gte=start_minute,
                timestamp__lte=end_minute,
            ).order_by('timestamp')[:10000]
            self._fill_buckets(labels, values, start_minute, storage_data, 'usage_pct')

        else:
            return Response({'status': 'error', 'message': f'Unknown metric: {metric}'}, status=400)

        return Response({
            'labels': labels,
            'datasets': [{'label': metric, 'data': values}],
        })
