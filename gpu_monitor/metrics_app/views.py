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
from .models import LatestSnapshot, MetricSnapshot
from rigs.models import Rig
from audit.middleware import log_audit_event

logger = logging.getLogger(__name__)


class IngestRateThrottle(SimpleRateThrottle):
    """Per-rig rate throttle — each rig_uuid gets its own budget.

    Reads rig_uuid from X-Rig-UUID header (always available, no body parsing needed).
    If header is missing, the request is not throttled (authentication will reject it).
    """

    scope = 'ingest'

    def get_cache_key(self, request, view):
        rig_uuid = request.META.get('HTTP_X_RIG_UUID', '')
        if not rig_uuid:
            # No rig_uuid — don't throttle, let authentication handle rejection
            return None
        return f'ingest_{rig_uuid}'


@method_decorator(csrf_exempt, name='dispatch')
class IngestView(APIView):
    """POST /api/v1/ingest/ — Accept telemetry payload from agents."""
    authentication_classes = [APIKeyAuthentication]
    throttle_classes = [IngestRateThrottle]

    # Timestamp sanity check thresholds
    MAX_FUTURE_S = 300   # 5 minutes
    MAX_PAST_S = 3600    # 1 hour

    def post(self, request):
        user = request.user
        api_key = request.auth
        data = request.data

        if not isinstance(data, dict):
            return Response({'status': 'error', 'message': 'Expected JSON object'}, status=400)

        rig_uuid = str(data.get('rig_uuid', ''))
        if not rig_uuid:
            return Response({'status': 'error', 'message': 'Missing rig_uuid'}, status=400)

        # ── Timestamp sanity check ──────────────────────────────────────
        ts = data.get('timestamp')
        if ts is not None:
            try:
                from datetime import datetime, timezone as dt_timezone
                from django.utils.dateparse import parse_datetime
                parsed = parse_datetime(str(ts))
                if parsed is not None:
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=dt_timezone.utc)
                    now = datetime.now(dt_timezone.utc)
                    diff = abs((parsed - now).total_seconds())
                    if diff > self.MAX_PAST_S:
                        return Response(
                            {'status': 'error', 'message': f'Timestamp too old: {ts}'},
                            status=400,
                        )
                    if parsed > now + __import__('datetime').timedelta(seconds=self.MAX_FUTURE_S):
                        return Response(
                            {'status': 'error', 'message': f'Timestamp too far in future: {ts}'},
                            status=400,
                        )
            except Exception:
                pass  # If parsing fails, let it through — process_ingest will handle it

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
                enrolled_by_api_key=api_key,
            )
            log_audit_event(request, 'rig.enrolled', 'Rig', rig.uuid,
                          {'agent_version': data.get('agent_version', ''), 'ip': request.META.get('REMOTE_ADDR')})
        else:
            if rig.owner_id != user.id:
                return Response({'status': 'error', 'message': 'UUID already claimed by another user'}, status=409)

        # Update enrolled_by_api_key to the current key (handles key rotation on the agent)
        # Combine with the last_seen/status update below to minimize DB writes
        enrolled_by_key_changed = rig.enrolled_by_api_key_id != api_key.id
        if enrolled_by_key_changed:
            rig.enrolled_by_api_key = api_key

        # Process the payload
        result, http_status = process_ingest(rig_uuid, data, user.id, rig=rig)

        # Update rig last_seen, status, and optionally enrolled_by_api_key
        rig.last_seen = timezone.now()
        rig.status = Rig.Status.ONLINE
        update_fields = ['last_seen', 'status']
        if enrolled_by_key_changed:
            update_fields.append('enrolled_by_api_key')
        rig.save(update_fields=update_fields)

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
                'cpu_freq_current_mhz': snapshot.cpu_freq_current_mhz,
                'cpu_freq_min_mhz': snapshot.cpu_freq_min_mhz,
                'cpu_freq_max_mhz': snapshot.cpu_freq_max_mhz,
                'mem_used_bytes': snapshot.mem_used_bytes,
                'mem_total_bytes': snapshot.mem_total_bytes,
            }
        except LatestSnapshot.DoesNotExist:
            data = {'rig_uuid': str(uuid), 'timestamp': None}

        return Response(data)


class ChartDataView(APIView):
    """GET /api/v1/rigs/<uuid>/chart-data/ — Historical chart data.

    Uses SQL-level aggregation (date_trunc + AVG/SUM) for all metrics.
    No Python-side bucket filling — the database does all the work.
    """
    authentication_classes = [SessionAuthentication]

    SNAPSHOT_METRICS = {
        'cpu_utilization_pct', 'cpu_temp_c', 'cpu_freq_current_mhz',
        'mem_total_bytes', 'mem_used_bytes', 'mem_free_bytes', 'mem_cached_bytes',
        'swap_used_bytes', 'swap_total_bytes',
        'cpu_power_w', 'total_system_power_w',
    }
    GPU_METRICS = {
        'gpu_temp_c': 'gpu_temp_c', 'gpu_util_pct': 'gpu_util_pct',
        'gpu_mem_used_mb': 'mem_used_mb', 'gpu_mem_total_mb': 'mem_total_mb',
        'gpu_power_w': 'power_draw_w', 'gpu_power_limit_w': 'power_limit_w',
        'gpu_fan_pct': 'fan_speed_pct',
        'gpu_core_clock_mhz': 'gpu_core_clock_mhz', 'gpu_mem_clock_mhz': 'gpu_mem_clock_mhz',
    }
    STORAGE_METRICS = {'disk_usage_pct'}
    DISK_IO_METRICS = {
        'disk_read_bytes_delta': 'read_bytes_delta',
        'disk_write_bytes_delta': 'write_bytes_delta',
        'disk_read_iops_delta': 'read_iops_delta',
        'disk_write_iops_delta': 'write_iops_delta',
        'disk_utilization_pct': 'utilization_pct',
    }
    DISK_BYTE_METRICS = {'disk_read_bytes_delta', 'disk_write_bytes_delta'}
    BYTE_TO_GB = {'mem_total_bytes', 'mem_used_bytes', 'mem_free_bytes', 'mem_cached_bytes', 'swap_used_bytes', 'swap_total_bytes'}
    BYTE_TO_MB = {'rx_bytes_delta', 'tx_bytes_delta'}

    def _build_buckets(self, range_hours, bucket_minutes=1):
        now = timezone.now()
        end_bucket = now.replace(second=0, microsecond=0)
        end_bucket -= timedelta(minutes=end_bucket.minute % bucket_minutes)
        total_buckets = (range_hours * 60) // bucket_minutes
        start_bucket = end_bucket - timedelta(minutes=total_buckets * bucket_minutes)
        labels = []
        for i in range(total_buckets):
            t = start_bucket + timedelta(minutes=i * bucket_minutes)
            labels.append(t.strftime('%m-%d %H:%M') if range_hours > 24 or bucket_minutes >= 60 else t.strftime('%H:%M'))
        return labels, start_bucket, end_bucket

    def get(self, request, uuid):
        from django.db.models import Avg, Sum, Count
        from django.db.models.functions import TruncMinute, TruncHour

        user = request.user
        rig = get_object_or_404(Rig, uuid=uuid)
        if rig.owner_id != user.id and not request.user.is_staff:
            return Response({'status': 'error', 'message': 'Forbidden'}, status=403)

        metric = request.query_params.get('metric', 'cpu_utilization_pct')
        range_hours = int(request.query_params.get('range', 24))
        gpu_index = int(request.query_params.get('gpu_index', 0))
        multi_gpu = request.query_params.get('multi_gpu', 'false').lower() == 'true'
        multi_disk = request.query_params.get('multi_disk', 'false').lower() == 'true'
        multi_iface = request.query_params.get('multi_iface', 'false').lower() == 'true'

        multi_mem = request.query_params.get('multi_mem', 'false').lower() == 'true'

        # Bucket size: 1-min for 24h, 1-hour for 7d/30d
        bucket_minutes = 1 if range_hours <= 24 else 60
        labels, start_bucket, end_bucket = self._build_buckets(range_hours, bucket_minutes)
        total_buckets = len(labels)
        trunc = TruncMinute if bucket_minutes == 1 else TruncHour
        agg_func = Sum if metric in {'net_rx_bytes_delta', 'net_tx_bytes_delta', 'net_rx_errors', 'net_tx_errors', 'error_frequency', 'disk_read_bytes_delta', 'disk_write_bytes_delta', 'disk_read_iops_delta', 'disk_write_iops_delta'} else Avg

        # Helper: run SQL aggregation and map to values array
        def chart_values(qs, field):
            data = list(qs.annotate(bucket=trunc('timestamp')).values('bucket').annotate(val=agg_func(field)).order_by('bucket'))
            values = [None] * total_buckets
            for row in data:
                idx = int((row['bucket'] - start_bucket).total_seconds() // (bucket_minutes * 60))
                if 0 <= idx < total_buckets:
                    values[idx] = round(row['val'], 2) if row['val'] is not None else None
            return values

        base_filter = dict(rig_uuid=str(uuid), timestamp__gte=start_bucket, timestamp__lte=end_bucket)

        if metric in self.SNAPSHOT_METRICS:
            if multi_mem:
                mem_fields = {'mem_used_bytes': 'Memory Used', 'mem_free_bytes': 'Memory Free', 'swap_used_bytes': 'Swap Used'}
                datasets = []
                for field, label in mem_fields.items():
                    v = chart_values(MetricSnapshot.objects.filter(**base_filter), field)
                    v = [round(x / (1024**3), 2) if x is not None else None for x in v]
                    datasets.append({'label': label, 'data': v})
            else:
                values = chart_values(MetricSnapshot.objects.filter(**base_filter), metric)
                if metric in self.BYTE_TO_GB:
                    values = [round(v / (1024**3), 2) if v is not None else None for v in values]
                datasets = [{'label': metric, 'data': values}]

        elif metric == 'cpu_load_avg':
            snapshots = list(MetricSnapshot.objects.filter(**base_filter).order_by('timestamp'))
            load_datasets = [{'label': f'Load {m}m', 'data': [None]*total_buckets} for m in [1, 5, 15]]
            for s in snapshots:
                ts = s.timestamp.replace(second=0, microsecond=0)
                idx = int((ts - start_bucket).total_seconds() // (bucket_minutes * 60))
                if 0 <= idx < total_buckets and s.cpu_load_avg_json:
                    for i in range(min(3, len(s.cpu_load_avg_json))):
                        load_datasets[i]['data'][idx] = s.cpu_load_avg_json[i]
            datasets = load_datasets

        elif metric == 'uptime_s':
            snapshots = list(MetricSnapshot.objects.filter(**base_filter).order_by('timestamp'))
            values = [None] * total_buckets
            for s in snapshots:
                ts = s.timestamp.replace(second=0, microsecond=0)
                idx = int((ts - start_bucket).total_seconds() // (bucket_minutes * 60))
                if 0 <= idx < total_buckets and s.uptime_s is not None:
                    values[idx] = round(s.uptime_s / 86400, 2)
            datasets = [{'label': 'Uptime (days)', 'data': values}]

        elif metric in self.GPU_METRICS:
            from .models import GPUMetric
            fn = self.GPU_METRICS[metric]
            base_qs = GPUMetric.objects.filter(**base_filter)
            if multi_gpu:
                datasets = [{'label': f'GPU{gpu_index}', 'data': chart_values(base_qs.filter(gpu_index=gpu_index), fn)}
                            for gpu_index in base_qs.values_list('gpu_index', flat=True).distinct().order_by('gpu_index')]
            else:
                datasets = [{'label': f'GPU {gpu_index}', 'data': chart_values(base_qs.filter(gpu_index=gpu_index), fn)}]

        elif metric in self.STORAGE_METRICS:
            from .models import StorageMetric
            base_qs = StorageMetric.objects.filter(**base_filter)
            if multi_disk:
                datasets = [{'label': dev, 'data': chart_values(base_qs.filter(device=dev), 'usage_pct')}
                            for dev in base_qs.values_list('device', flat=True).distinct().order_by('device')]
            else:
                datasets = [{'label': 'Disk Usage %', 'data': chart_values(base_qs, 'usage_pct')}]

        elif metric in self.DISK_IO_METRICS:
            from .models import StorageMetric
            fn = self.DISK_IO_METRICS[metric]
            base_qs = StorageMetric.objects.filter(**base_filter)
            byte_metric = metric in self.DISK_BYTE_METRICS
            if multi_disk:
                datasets = []
                for dev in base_qs.values_list('device', flat=True).distinct().order_by('device'):
                    v = chart_values(base_qs.filter(device=dev), fn)
                    if byte_metric:
                        v = [round(x / (1024*1024), 2) if x is not None else None for x in v]
                    datasets.append({'label': dev, 'data': v})
            else:
                v = chart_values(base_qs, fn)
                if byte_metric:
                    v = [round(x / (1024*1024), 2) if x is not None else None for x in v]
                label_map = {
                    'disk_read_bytes_delta': 'Read MB',
                    'disk_write_bytes_delta': 'Write MB',
                    'disk_read_iops_delta': 'Read IOPS',
                    'disk_write_iops_delta': 'Write IOPS',
                    'disk_utilization_pct': 'Utilization %',
                }
                datasets = [{'label': label_map.get(metric, metric), 'data': v}]

        elif metric.startswith('net_'):
            from .models import NetworkMetric
            fn = {'net_rx_bytes_delta': 'rx_bytes_delta', 'net_tx_bytes_delta': 'tx_bytes_delta',
                  'net_rx_errors': 'rx_errors', 'net_tx_errors': 'tx_errors'}.get(metric)
            if not fn:
                return Response({'status': 'error', 'message': f'Unknown network metric: {metric}'}, status=400)
            base_qs = NetworkMetric.objects.filter(**base_filter)
            byte_metric = fn in self.BYTE_TO_MB
            if multi_iface:
                datasets = []
                for iface in base_qs.values_list('interface', flat=True).distinct().order_by('interface'):
                    v = chart_values(base_qs.filter(interface=iface), fn)
                    if byte_metric:
                        v = [round(x / (1024*1024), 2) if x is not None else None for x in v]
                    datasets.append({'label': iface, 'data': v})
            else:
                v = chart_values(base_qs.filter(interface__isnull=False), fn)
                if byte_metric:
                    v = [round(x / (1024*1024), 2) if x is not None else None for x in v]
                datasets = [{'label': metric, 'data': v}]

        elif metric == 'error_frequency':
            data = list(MetricSnapshot.objects.filter(**base_filter)
                        .annotate(bucket=trunc('timestamp')).values('bucket').annotate(errors=Sum('error_count')).order_by('bucket'))
            values = [0] * total_buckets
            for row in data:
                idx = int((row['bucket'] - start_bucket).total_seconds() // (bucket_minutes * 60))
                if 0 <= idx < total_buckets:
                    values[idx] = row['errors'] or 0
            label = 'Errors/min' if bucket_minutes == 1 else 'Errors/hour'
            datasets = [{'label': label, 'data': values}]

        else:
            return Response({'status': 'error', 'message': f'Unknown metric: {metric}'}, status=400)

        return Response({'labels': labels, 'datasets': datasets})


class ReportDataView(APIView):
    """GET /api/v1/rigs/<uuid>/report-data/ — Aggregated report data.

    Returns scalar aggregates (AVG, MAX, SUM) for all metrics over a time range.
    Used by the Report tab in rig_detail to display summary tables.
    """
    authentication_classes = [SessionAuthentication]

    def get(self, request, uuid):
        from django.db.models import Avg, Max, Sum, F
        from django.db.models.functions import Cast
        from django.db.models import FloatField

        user = request.user
        rig = get_object_or_404(Rig, uuid=uuid)
        if rig.owner_id != user.id and not request.user.is_staff:
            return Response({'status': 'error', 'message': 'Forbidden'}, status=403)

        range_hours = int(request.query_params.get('range_hours', 24))
        if range_hours not in (24, 168, 720):
            return Response({'status': 'error', 'message': 'Invalid range. Use 24, 168, or 720.'}, status=400)

        now = timezone.now()
        start = now - timedelta(hours=range_hours)
        base_filter = dict(rig_uuid=str(uuid), timestamp__gte=start, timestamp__lte=now)

        result = {'range_hours': range_hours}

        # -- GPU metrics (GPUMetric) --
        gpu_qs = GPUMetric.objects.filter(**base_filter)
        gpu_agg = gpu_qs.aggregate(
            gpu_temp_c_avg=Avg('gpu_temp_c'),
            gpu_temp_c_max=Max('gpu_temp_c'),
            gpu_util_pct_avg=Avg('gpu_util_pct'),
            gpu_util_pct_max=Max('gpu_util_pct'),
            power_draw_w_avg=Avg('power_draw_w'),
            power_draw_w_max=Max('power_draw_w'),
            mem_used_mb_avg=Avg('mem_used_mb'),
            mem_used_mb_max=Max('mem_used_mb'),
            fan_speed_pct_avg=Avg('fan_speed_pct'),
            fan_speed_pct_max=Max('fan_speed_pct'),
            gpu_core_clock_mhz_avg=Avg('gpu_core_clock_mhz'),
            gpu_mem_clock_mhz_avg=Avg('gpu_mem_clock_mhz'),
        )
        result.update(gpu_agg)

        # -- CPU / Memory / Power / System metrics (MetricSnapshot) --
        snap_qs = MetricSnapshot.objects.filter(**base_filter)
        snap_agg = snap_qs.aggregate(
            cpu_utilization_pct_avg=Avg('cpu_utilization_pct'),
            cpu_utilization_pct_max=Max('cpu_utilization_pct'),
            cpu_temp_c_avg=Avg('cpu_temp_c'),
            cpu_temp_c_max=Max('cpu_temp_c'),
            cpu_power_w_avg=Avg('cpu_power_w'),
            cpu_power_w_max=Max('cpu_power_w'),
            cpu_freq_current_mhz_avg=Avg('cpu_freq_current_mhz'),
            mem_used_bytes_avg=Avg('mem_used_bytes'),
            mem_used_bytes_max=Max('mem_used_bytes'),
            swap_used_bytes_avg=Avg('swap_used_bytes'),
            total_system_power_w_avg=Avg('total_system_power_w'),
            total_system_power_w_max=Max('total_system_power_w'),
            error_count_sum=Sum('error_count'),
            uptime_s_max=Max('uptime_s'),
        )
        result.update(snap_agg)

        # -- Disk metrics (StorageMetric) --
        disk_qs = StorageMetric.objects.filter(**base_filter)
        disk_agg = disk_qs.aggregate(
            disk_usage_pct_max=Max('usage_pct'),
            disk_read_bytes_sum=Sum('read_bytes_delta'),
            disk_write_bytes_sum=Sum('write_bytes_delta'),
            disk_read_iops_max=Max('read_iops_delta'),
            disk_write_iops_max=Max('write_iops_delta'),
            disk_utilization_pct_max=Max('utilization_pct'),
        )
        result.update(disk_agg)

        # -- Network metrics (NetworkMetric) --
        net_qs = NetworkMetric.objects.filter(**base_filter)
        net_agg = net_qs.aggregate(
            net_rx_bytes_sum=Sum('rx_bytes_delta'),
            net_tx_bytes_sum=Sum('tx_bytes_delta'),
            net_rx_errors_sum=Sum('rx_errors'),
            net_tx_errors_sum=Sum('tx_errors'),
        )
        result.update(net_agg)

        # Convert None to 0 for cleaner display
        for key, val in result.items():
            if val is None and key != 'range_hours':
                result[key] = 0

        # Derive uptime in days from seconds
        if result.get('uptime_s_max'):
            result['uptime_days_max'] = round(result['uptime_s_max'] / 86400, 1)

        return Response(result)