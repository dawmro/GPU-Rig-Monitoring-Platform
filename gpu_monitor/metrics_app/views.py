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

        # Process the payload
        result, http_status = process_ingest(rig_uuid, data, user.id, rig=rig)

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
    """GET /api/v1/rigs/<uuid>/metrics/ — Latest metrics for a rig.

    Returns the latest snapshot values from the denormalized LatestSnapshot table.
    For full time-series data, use the chart-data endpoint.
    """
    authentication_classes = [SessionAuthentication]

    def get(self, request, uuid):
        user = request.user
        rig = get_object_or_404(Rig, uuid=uuid)
        if rig.owner_id != user.id and not request.user.is_staff:
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
            }
        except LatestSnapshot.DoesNotExist:
            data = {'rig_uuid': str(uuid), 'timestamp': None}

        return Response(data)


class ChartDataView(APIView):
    """GET /api/v1/rigs/<uuid>/chart-data/ — Historical chart data.

    Returns fixed 1-minute buckets for the requested range (default 24h = 1440 points).
    Empty buckets are null-filled so offline periods are visible on the chart.

    Query parameters:
        metric: Field name to chart. Supported values:
            - cpu_utilization_pct, cpu_temp_c, cpu_load_avg (from MetricSnapshot)
            - mem_used_bytes, mem_total_bytes, mem_free_bytes, mem_cached_bytes,
              swap_used_bytes, swap_total_bytes (from MetricSnapshot)
            - gpu_temp_c, gpu_util_pct, gpu_mem_used_mb, gpu_power_w, gpu_fan_pct
              (from GPUMetric; single GPU via gpu_index or all GPUs via multi_gpu)
            - disk_usage_pct (from StorageMetric, first disk)
        range: Hours of historical data (default: 24)
        gpu_index: GPU index for single-GPU metrics (default: 0)
        multi_gpu: If 'true', query all GPUs and return one dataset per GPU UUID
    """
    authentication_classes = [SessionAuthentication]

    # Metrics stored directly on MetricSnapshot (single value per row)
    SNAPSHOT_METRICS = {
        'cpu_utilization_pct', 'cpu_temp_c',
        'mem_total_bytes', 'mem_used_bytes', 'mem_free_bytes', 'mem_cached_bytes',
        'swap_used_bytes', 'swap_total_bytes',
    }
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

    # Byte metrics that should be converted to GB in the response
    BYTE_TO_GB = {
        'mem_total_bytes', 'mem_used_bytes', 'mem_free_bytes', 'mem_cached_bytes',
        'swap_used_bytes', 'swap_total_bytes',
    }

    def _build_buckets(self, range_hours):
        """Build fixed 1-minute bucket labels and empty value array.

        Returns (labels, values) where labels are 'HH:MM' strings and
        values are a list of Nones with length = range_hours * 60.
        Bucket 0 = oldest, bucket N-1 = newest (now).
        """
        now = timezone.now()
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
        """Fill bucket values from a queryset (single-value metrics).

        Each row is placed into the bucket matching its truncated minute.
        If multiple rows fall in the same bucket, the last one wins.
        """
        total_minutes = len(labels)
        for row in queryset:
            ts = getattr(row, value_key)
            ts_minute = ts.replace(second=0, microsecond=0)
            delta = ts_minute - start_minute
            idx = int(delta.total_seconds() // 60)
            if 0 <= idx < total_minutes:
                val = getattr(row, field_name, None)
                if val is not None:
                    values[idx] = val

    def _fill_buckets_multi(self, labels, datasets, start_minute, queryset, field_name, value_key='timestamp'):
        """Fill bucket values for multi-value metrics (e.g. load_avg with 3 values).

        datasets is a list of dicts: [{'label': '1min', 'data': [...]}, ...]
        The field is expected to be a JSON array.
        """
        total_minutes = len(labels)
        num_values = len(datasets)
        for row in queryset:
            ts = getattr(row, value_key)
            ts_minute = ts.replace(second=0, microsecond=0)
            delta = ts_minute - start_minute
            idx = int(delta.total_seconds() // 60)
            if 0 <= idx < total_minutes:
                val = getattr(row, field_name, None)
                if val and isinstance(val, (list, tuple)) and len(val) >= num_values:
                    for i in range(num_values):
                        datasets[i]['data'][idx] = val[i]

    def _fill_buckets_multi_gpu(self, labels, datasets, start_minute, queryset, field_name, value_key='timestamp'):
        """Fill bucket values for multi-GPU metrics (one dataset per GPU UUID).

        datasets is a list of dicts: [{'label': 'GPU-a322cff...', 'data': [...]}, ...]
        Rows are matched to datasets by gpu_uuid.
        """
        total_minutes = len(labels)
        uuid_to_idx = {ds['label']: i for i, ds in enumerate(datasets)}
        for row in queryset:
            ts = getattr(row, value_key)
            ts_minute = ts.replace(second=0, microsecond=0)
            delta = ts_minute - start_minute
            idx = int(delta.total_seconds() // 60)
            if 0 <= idx < total_minutes:
                val = getattr(row, field_name, None)
                gpu_uuid = row.gpu_uuid
                if val is not None and gpu_uuid in uuid_to_idx:
                    datasets[uuid_to_idx[gpu_uuid]]['data'][idx] = val

    def get(self, request, uuid):
        user = request.user
        rig = get_object_or_404(Rig, uuid=uuid)
        if rig.owner_id != user.id and not request.user.is_staff:
            return Response({'status': 'error', 'message': 'Forbidden'}, status=403)

        metric = request.query_params.get('metric', 'cpu_utilization_pct')
        range_hours = int(request.query_params.get('range', '24'))
        gpu_index = int(request.query_params.get('gpu_index', 0))
        multi_gpu = request.query_params.get('multi_gpu', 'false').lower() == 'true'

        labels, values, start_minute, end_minute = self._build_buckets(range_hours)

        if metric in self.SNAPSHOT_METRICS:
            snapshots = MetricSnapshot.objects.filter(
                rig_uuid=str(uuid),
                timestamp__gte=start_minute,
                timestamp__lte=end_minute,
            ).order_by('timestamp')[:10000]
            self._fill_buckets(labels, values, start_minute, snapshots, metric)
            if metric in self.BYTE_TO_GB:
                values = [round(v / (1024**3), 2) if v is not None else None for v in values]
            datasets = [{'label': metric, 'data': values}]

        elif metric == 'cpu_load_avg':
            snapshots = MetricSnapshot.objects.filter(
                rig_uuid=str(uuid),
                timestamp__gte=start_minute,
                timestamp__lte=end_minute,
            ).order_by('timestamp')[:10000]
            load_datasets = [
                {'label': 'Load 1m', 'data': [None] * len(labels)},
                {'label': 'Load 5m', 'data': [None] * len(labels)},
                {'label': 'Load 15m', 'data': [None] * len(labels)},
            ]
            self._fill_buckets_multi(labels, load_datasets, start_minute, snapshots, 'cpu_load_avg_json')
            datasets = load_datasets

        elif metric in self.GPU_METRICS:
            from .models import GPUMetric
            field_name = self.GPU_METRICS[metric]
            if multi_gpu:
                # Discover unique GPU UUIDs first (separate query, no slice)
                seen_uuids = list(
                    GPUMetric.objects.filter(
                        rig_uuid=str(uuid),
                        timestamp__gte=start_minute,
                        timestamp__lte=end_minute,
                    ).order_by('gpu_uuid').values_list('gpu_uuid', flat=True).distinct()
                )
                # Build one dataset per GPU UUID
                gpu_datasets = [
                    {'label': guuid, 'data': [None] * len(labels)}
                    for guuid in seen_uuids
                ]
                # Query all GPU data (with slice for safety)
                gpu_data = GPUMetric.objects.filter(
                    rig_uuid=str(uuid),
                    timestamp__gte=start_minute,
                    timestamp__lte=end_minute,
                ).order_by('timestamp')[:50000]
                self._fill_buckets_multi_gpu(labels, gpu_datasets, start_minute, gpu_data, field_name)
                datasets = gpu_datasets
            else:
                # Single GPU mode (backward compatible)
                gpu_data = GPUMetric.objects.filter(
                    rig_uuid=str(uuid),
                    gpu_index=gpu_index,
                    timestamp__gte=start_minute,
                    timestamp__lte=end_minute,
                ).order_by('timestamp')[:10000]
                self._fill_buckets(labels, values, start_minute, gpu_data, field_name)
                datasets = [{'label': f'GPU {gpu_index}', 'data': values}]

        elif metric in self.STORAGE_METRICS:
            from .models import StorageMetric
            storage_data = StorageMetric.objects.filter(
                rig_uuid=str(uuid),
                timestamp__gte=start_minute,
                timestamp__lte=end_minute,
            ).order_by('timestamp')[:10000]
            self._fill_buckets(labels, values, start_minute, storage_data, 'usage_pct')
            datasets = [{'label': metric, 'data': values}]

        else:
            return Response({'status': 'error', 'message': f'Unknown metric: {metric}'}, status=400)

        return Response({
            'labels': labels,
            'datasets': datasets,
        })
