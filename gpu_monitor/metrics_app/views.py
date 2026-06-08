import logging
import statistics
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
from .models import LatestSnapshot, MetricSnapshot, ErrorEvent, ErrorEventOccurrence, DockerContainerMetric, AIProcessMetric
from rigs.models import Rig
from audit.middleware import log_audit_event

logger = logging.getLogger(__name__)


class IngestRateThrottle(SimpleRateThrottle):
    """Per-rig rate throttle — each rig_uuid gets its own budget."""

    scope = 'ingest'

    def get_cache_key(self, request, view):
        # Throttle per rig_uuid so N rigs each get the full rate
        rig_uuid = ''
        if hasattr(request, 'data') and isinstance(request.data, dict):
            rig_uuid = str(request.data.get('rig_uuid', ''))
        if not rig_uuid:
            # Fallback to IP for unauthenticated requests
            rig_uuid = self.get_ident(request)
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
    # Byte-delta metrics that should be converted to MB/s in the response
    BYTE_TO_MB = {'rx_bytes_delta', 'tx_bytes_delta'}

    def _build_buckets(self, range_hours, bucket_minutes=1):
        """Build fixed bucket labels and empty value array.

        Returns (labels, values, start_bucket, end_bucket) where labels are
        time strings and values are a list of Nones.
        Bucket 0 = oldest, bucket N-1 = newest (now).

        Args:
            range_hours: Total hours of historical data
            bucket_minutes: Size of each bucket in minutes (1, 15, or 60)
        """
        now = timezone.now()
        end_bucket = now.replace(second=0, microsecond=0)
        # Align end_bucket to bucket boundary
        end_bucket = end_bucket - timedelta(minutes=end_bucket.minute % bucket_minutes)
        total_buckets = (range_hours * 60) // bucket_minutes
        start_bucket = end_bucket - timedelta(minutes=total_buckets * bucket_minutes)

        labels = []
        for i in range(total_buckets):
            bucket_time = start_bucket + timedelta(minutes=i * bucket_minutes)
            # For ranges > 24h or buckets >= 1h, include date in label
            if range_hours > 24 or bucket_minutes >= 60:
                labels.append(bucket_time.strftime('%m-%d %H:%M'))
            else:
                labels.append(bucket_time.strftime('%H:%M'))

        values = [None] * total_buckets
        return labels, values, start_bucket, end_bucket

    def _fill_buckets(self, labels, values, start_bucket, queryset, field_name,
                      value_key='timestamp', bucket_minutes=1, aggregate=None):
        """Fill bucket values from a queryset.

        For bucket_minutes=1: Each row is placed into the bucket matching its
        truncated minute. Last value wins.

        For bucket_minutes>1: Multiple rows per bucket are collected, and the
        aggregate function is applied (default: median).

        Args:
            labels: Bucket label list
            values: Output values list (modified in place)
            start_bucket: Datetime of the first bucket
            queryset: Django queryset to pull rows from
            field_name: Model field to extract values from
            value_key: Timestamp field name (default: 'timestamp')
            bucket_minutes: Size of each bucket in minute (default: 1)
            aggregate: Aggregation function for multi-row buckets:
                       'median', 'avg', 'max', 'min', or None (last value wins)
        """
        total_buckets = len(labels)
        if total_buckets == 0:
            return

        if bucket_minutes == 1 and aggregate is None:
            # Fast path: 1-minute buckets, last value wins
            for row in queryset:
                ts = getattr(row, value_key)
                ts_minute = ts.replace(second=0, microsecond=0)
                delta = ts_minute - start_bucket
                idx = int(delta.total_seconds() // 60)
                if 0 <= idx < total_buckets:
                    val = getattr(row, field_name, None)
                    if val is not None:
                        values[idx] = val
        else:
            # Aggregate path: collect all values per bucket
            bucket_values = [[] for _ in range(total_buckets)]
            bucket_seconds = bucket_minutes * 60
            for row in queryset:
                ts = getattr(row, value_key)
                ts = ts.replace(second=0, microsecond=0)
                delta = ts - start_bucket
                idx = int(delta.total_seconds() // bucket_seconds)
                if 0 <= idx < total_buckets:
                    val = getattr(row, field_name, None)
                    if val is not None:
                        bucket_values[idx].append(val)

            # Apply aggregation
            for i in range(total_buckets):
                if bucket_values[i]:
                    if aggregate == 'median':
                        values[i] = round(statistics.median(bucket_values[i]), 2)
                    elif aggregate == 'avg':
                        values[i] = round(sum(bucket_values[i]) / len(bucket_values[i]), 2)
                    elif aggregate == 'sum':
                        values[i] = round(sum(bucket_values[i]), 2)
                    elif aggregate == 'max':
                        values[i] = max(bucket_values[i])
                    elif aggregate == 'min':
                        values[i] = min(bucket_values[i])
                    else:
                        values[i] = bucket_values[i][-1]  # last value wins

    def _fill_buckets_multi(self, labels, datasets, start_bucket, queryset, field_name,
                            value_key='timestamp', bucket_minutes=1, aggregate=None):
        """Fill bucket values for multi-value metrics (e.g. load_avg with 3 values).

        datasets is a list of dicts: [{'label': '1min', 'data': [...]}, ...]
        The field is expected to be a JSON array.
        """
        total_buckets = len(labels)
        if total_buckets == 0:
            return
        num_values = len(datasets)
        bucket_seconds = bucket_minutes * 60

        if bucket_minutes == 1 and aggregate is None:
            # Fast path: 1-minute buckets
            for row in queryset:
                ts = getattr(row, value_key)
                ts_minute = ts.replace(second=0, microsecond=0)
                delta = ts_minute - start_bucket
                idx = int(delta.total_seconds() // 60)
                if 0 <= idx < total_buckets:
                    val = getattr(row, field_name, None)
                    if val and isinstance(val, (list, tuple)) and len(val) >= num_values:
                        for i in range(num_values):
                            datasets[i]['data'][idx] = val[i]
        else:
            # Aggregate path: collect all values per bucket
            bucket_values = [[[] for _ in range(num_values)] for _ in range(total_buckets)]
            for row in queryset:
                ts = getattr(row, value_key)
                ts = ts.replace(second=0, microsecond=0)
                delta = ts - start_bucket
                idx = int(delta.total_seconds() // bucket_seconds)
                if 0 <= idx < total_buckets:
                    val = getattr(row, field_name, None)
                    if val and isinstance(val, (list, tuple)) and len(val) >= num_values:
                        for i in range(num_values):
                            bucket_values[idx][i].append(val[i])
            for i in range(total_buckets):
                for j in range(num_values):
                    if bucket_values[i][j]:
                        if aggregate == 'median':
                            datasets[j]['data'][i] = round(statistics.median(bucket_values[i][j]), 2)
                        elif aggregate == 'avg':
                            datasets[j]['data'][i] = round(sum(bucket_values[i][j]) / len(bucket_values[i][j]), 2)
                        elif aggregate == 'sum':
                            datasets[j]['data'][i] = round(sum(bucket_values[i][j]), 2)
                        else:
                            datasets[j]['data'][i] = bucket_values[i][j][-1]

    def _fill_buckets_from_values(self, labels, values, start_bucket, queryset, field_values,
                                  value_key='timestamp', bucket_minutes=1, aggregate=None):
        """Fill bucket values from a parallel list of values (one per queryset row)."""
        total_buckets = len(labels)
        rows = list(queryset)
        bucket_seconds = bucket_minutes * 60

        if bucket_minutes == 1 and aggregate is None:
            # Fast path: 1-minute buckets, last value wins
            for i, row in enumerate(rows):
                if i >= len(field_values):
                    break
                ts = getattr(row, value_key)
                ts = ts.replace(second=0, microsecond=0)
                delta = ts - start_bucket
                idx = int(delta.total_seconds() // 60)
                if 0 <= idx < total_buckets:
                    val = field_values[i]
                    if val is not None:
                        values[idx] = val
        else:
            # Aggregate path: collect all values per bucket
            bucket_values = [[] for _ in range(total_buckets)]
            for i, row in enumerate(rows):
                if i >= len(field_values):
                    break
                ts = getattr(row, value_key)
                ts = ts.replace(second=0, microsecond=0)
                delta = ts - start_bucket
                idx = int(delta.total_seconds() // bucket_seconds)
                if 0 <= idx < total_buckets:
                    val = field_values[i]
                    if val is not None:
                        bucket_values[idx].append(val)
            for i in range(total_buckets):
                if bucket_values[i]:
                    if aggregate == 'median':
                        values[i] = round(statistics.median(bucket_values[i]), 2)
                    elif aggregate == 'avg':
                        values[i] = round(sum(bucket_values[i]) / len(bucket_values[i]), 2)
                    elif aggregate == 'sum':
                        values[i] = round(sum(bucket_values[i]), 2)
                    elif aggregate == 'max':
                        values[i] = max(bucket_values[i])
                    elif aggregate == 'min':
                        values[i] = min(bucket_values[i])
                    else:
                        values[i] = bucket_values[i][-1]

    def _fill_buckets_multi_key(self, labels, datasets, start_bucket, queryset, field_name, key_field,
                                value_key='timestamp', bucket_minutes=1, aggregate=None):
        """Fill bucket values for multi-key metrics (one dataset per unique key value).

        Used for multi-GPU (key_field='gpu_uuid'), multi-disk (key_field='device'),
        and multi-interface (key_field='interface').

        datasets is a list of dicts with '_key' matching key_field values.
        The 'label' field is the display label (may include extra info like model name).
        """
        total_buckets = len(labels)
        if total_buckets == 0:
            return
        key_to_idx = {ds['_key']: i for i, ds in enumerate(datasets)}
        bucket_seconds = bucket_minutes * 60

        if bucket_minutes == 1 and aggregate is None:
            # Fast path: 1-minute buckets
            for row in queryset:
                ts = getattr(row, value_key)
                ts_minute = ts.replace(second=0, microsecond=0)
                delta = ts_minute - start_bucket
                idx = int(delta.total_seconds() // 60)
                if 0 <= idx < total_buckets:
                    val = getattr(row, field_name, None)
                    key_val = getattr(row, key_field, None)
                    if val is not None and key_val in key_to_idx:
                        datasets[key_to_idx[key_val]]['data'][idx] = val
        else:
            # Aggregate path: collect all values per bucket per key
            bucket_values = {ds['_key']: [[] for _ in range(total_buckets)] for ds in datasets}
            for row in queryset:
                ts = getattr(row, value_key)
                ts = ts.replace(second=0, microsecond=0)
                delta = ts - start_bucket
                idx = int(delta.total_seconds() // bucket_seconds)
                if 0 <= idx < total_buckets:
                    val = getattr(row, field_name, None)
                    key_val = getattr(row, key_field, None)
                    if val is not None and key_val in key_to_idx:
                        bucket_values[key_val][idx].append(val)
            for key_val, idx_list in bucket_values.items():
                ds_idx = key_to_idx[key_val]
                for i in range(total_buckets):
                    if idx_list[i]:
                        if aggregate == 'median':
                            datasets[ds_idx]['data'][i] = round(statistics.median(idx_list[i]), 2)
                        elif aggregate == 'avg':
                            datasets[ds_idx]['data'][i] = round(sum(idx_list[i]) / len(idx_list[i]), 2)
                        elif aggregate == 'sum':
                            datasets[ds_idx]['data'][i] = round(sum(idx_list[i]), 2)
                        else:
                            datasets[ds_idx]['data'][i] = idx_list[i][-1]

    def get(self, request, uuid):
        user = request.user
        rig = get_object_or_404(Rig, uuid=uuid)
        if rig.owner_id != user.id and not request.user.is_staff:
            return Response({'status': 'error', 'message': 'Forbidden'}, status=403)

        metric = request.query_params.get('metric', 'cpu_utilization_pct')
        range_hours = int(request.query_params.get('range', '24'))
        gpu_index = int(request.query_params.get('gpu_index', 0))
        multi_gpu = request.query_params.get('multi_gpu', 'false').lower() == 'true'
        multi_disk = request.query_params.get('multi_disk', 'false').lower() == 'true'
        multi_iface = request.query_params.get('multi_iface', 'false').lower() == 'true'

        # Determine bucket size and aggregation based on range
        bucket_minutes = int(request.query_params.get('bucket_minutes', '1'))
        aggregate = None
        if bucket_minutes > 1:
            aggregate = 'avg'  # Default: average for aggregated buckets

        # Per-metric aggregation overrides
        # Byte-delta metrics (network) and error counts should use sum, not median
        SUM_AGGREGATE_METRICS = {
            'net_rx_bytes_delta', 'net_tx_bytes_delta',  # Network byte deltas (URL param names)
            'error_frequency',                             # Error counts
        }
        if metric in SUM_AGGREGATE_METRICS and bucket_minutes > 1:
            aggregate = 'sum'

        labels, values, start_bucket, end_bucket = self._build_buckets(range_hours, bucket_minutes)

        # Store for use in helper methods that don't receive these params
        self._bucket_minutes = bucket_minutes
        self._aggregate = aggregate

        if metric in self.SNAPSHOT_METRICS:
            snapshots = MetricSnapshot.objects.filter(
                rig_uuid=str(uuid),
                timestamp__gte=start_bucket,
                timestamp__lte=end_bucket,
            ).order_by('timestamp')[:10000]
            # Special combined memory chart: return 3 datasets in one response
            multi_mem = request.query_params.get('multi_mem', 'false').lower() == 'true'
            if multi_mem:
                mem_fields = {
                    'mem_used_bytes': 'Memory Used',
                    'mem_free_bytes': 'Memory Free',
                    'swap_used_bytes': 'Swap Used',
                }
                mem_datasets = []
                for field, label in mem_fields.items():
                    vals = [None] * len(labels)
                    self._fill_buckets(labels, vals, start_bucket, snapshots, field, bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
                    vals_gb = [round(v / (1024**3), 2) if v is not None else None for v in vals]
                    mem_datasets.append({'label': label, 'data': vals_gb})
                datasets = mem_datasets
            else:
                self._fill_buckets(labels, values, start_bucket, snapshots, metric, bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
                if metric in self.BYTE_TO_GB:
                    values = [round(v / (1024**3), 2) if v is not None else None for v in values]
                datasets = [{'label': metric, 'data': values}]

        elif metric == 'uptime_s':
            # Uptime from software_json (resets on reboot)
            snapshots = MetricSnapshot.objects.filter(
                rig_uuid=str(uuid),
                timestamp__gte=start_bucket,
                timestamp__lte=end_bucket,
            ).order_by('timestamp')[:10000]
            uptime_values = []
            for s in snapshots:
                uptime_s = s.software_json.get('uptime_s') if isinstance(s.software_json, dict) else None
                uptime_days = round(uptime_s / 86400, 2) if uptime_s is not None else None
                uptime_values.append(uptime_days)
            # Fill buckets (last value per minute)
            self._fill_buckets_from_values(labels, values, start_bucket, snapshots, uptime_values, bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
            datasets = [{'label': 'Uptime (days)', 'data': values}]

        elif metric == 'cpu_load_avg':
            snapshots = MetricSnapshot.objects.filter(
                rig_uuid=str(uuid),
                timestamp__gte=start_bucket,
                timestamp__lte=end_bucket,
            ).order_by('timestamp')[:10000]
            load_datasets = [
                {'label': 'Load 1m', 'data': [None] * len(labels)},
                {'label': 'Load 5m', 'data': [None] * len(labels)},
                {'label': 'Load 15m', 'data': [None] * len(labels)},
            ]
            self._fill_buckets_multi(labels, load_datasets, start_bucket, snapshots, 'cpu_load_avg_json', bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
            datasets = load_datasets

        elif metric in self.GPU_METRICS:
            from .models import GPUMetric
            field_name = self.GPU_METRICS[metric]
            if multi_gpu:
                # Discover unique GPU UUIDs with their models
                gpu_info = (
                    GPUMetric.objects.filter(
                        rig_uuid=str(uuid),
                        timestamp__gte=start_bucket,
                        timestamp__lte=end_bucket,
                    )
                    .values('gpu_uuid', 'model')
                    .distinct()
                    .order_by('gpu_uuid')
                )
                seen_uuids = []
                uuid_to_model = {}
                for row in gpu_info:
                    guuid = row['gpu_uuid']
                    seen_uuids.append(guuid)
                    uuid_to_model[guuid] = row['model'] or ''
                gpu_datasets = [
                    {
                        '_key': guuid,
                        'label': f'{guuid} {uuid_to_model.get(guuid, "")}'.strip(),
                        'data': [None] * len(labels),
                    }
                    for guuid in seen_uuids
                ]
                gpu_data = GPUMetric.objects.filter(
                    rig_uuid=str(uuid),
                    timestamp__gte=start_bucket,
                    timestamp__lte=end_bucket,
                ).order_by('timestamp')[:50000]
                self._fill_buckets_multi_key(labels, gpu_datasets, start_bucket, gpu_data, field_name, 'gpu_uuid', bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
                datasets = gpu_datasets
            else:
                gpu_data = GPUMetric.objects.filter(
                    rig_uuid=str(uuid),
                    gpu_index=gpu_index,
                    timestamp__gte=start_bucket,
                    timestamp__lte=end_bucket,
                ).order_by('timestamp')[:10000]
                self._fill_buckets(labels, values, start_bucket, gpu_data, field_name, bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
                datasets = [{'label': f'GPU {gpu_index}', 'data': values}]

        elif metric in self.STORAGE_METRICS:
            from .models import StorageMetric
            field_name = 'usage_pct'  # Always usage_pct for storage
            if multi_disk:
                # Discover unique disk devices
                disk_info = (
                    StorageMetric.objects.filter(
                        rig_uuid=str(uuid),
                        timestamp__gte=start_bucket,
                        timestamp__lte=end_bucket,
                    )
                    .values('device', 'mountpoint')
                    .distinct()
                    .order_by('device')
                )
                seen_devices = []
                device_to_mount = {}
                for row in disk_info:
                    dev = row['device']
                    seen_devices.append(dev)
                    device_to_mount[dev] = row['mountpoint'] or ''
                disk_datasets = [
                    {
                        '_key': dev,
                        'label': dev if dev == device_to_mount.get(dev, '') else f'{dev} {device_to_mount.get(dev, "")}'.strip(),
                        'data': [None] * len(labels),
                    }
                    for dev in seen_devices
                ]
                storage_data = StorageMetric.objects.filter(
                    rig_uuid=str(uuid),
                    timestamp__gte=start_bucket,
                    timestamp__lte=end_bucket,
                ).order_by('timestamp')[:50000]
                self._fill_buckets_multi_key(labels, disk_datasets, start_bucket, storage_data, field_name, 'device', bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
                datasets = disk_datasets
            else:
                storage_data = StorageMetric.objects.filter(
                    rig_uuid=str(uuid),
                    timestamp__gte=start_bucket,
                    timestamp__lte=end_bucket,
                ).order_by('timestamp')[:10000]
                self._fill_buckets(labels, values, start_bucket, storage_data, field_name, bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
                datasets = [{'label': metric, 'data': values}]

        elif metric.startswith('net_'):
            from .models import NetworkMetric
            # Map chart metric names to NetworkMetric field names
            NET_FIELD_MAP = {
                'net_rx_bytes_delta': 'rx_bytes_delta',
                'net_tx_bytes_delta': 'tx_bytes_delta',
                'net_rx_errors': 'rx_errors',
                'net_tx_errors': 'tx_errors',
            }
            if metric not in NET_FIELD_MAP:
                return Response({'status': 'error', 'message': f'Unknown network metric: {metric}'}, status=400)
            field_name = NET_FIELD_MAP[metric]
            if multi_iface:
                # Discover unique interfaces
                iface_info = (
                    NetworkMetric.objects.filter(
                        rig_uuid=str(uuid),
                        timestamp__gte=start_bucket,
                        timestamp__lte=end_bucket,
                    )
                    .values('interface', 'ipv4')
                    .distinct()
                    .order_by('interface')
                )
                seen_ifaces = []
                iface_to_ip = {}
                for row in iface_info:
                    iface = row['interface']
                    seen_ifaces.append(iface)
                    iface_to_ip[iface] = row['ipv4'] or ''
                iface_datasets = [
                    {
                        '_key': iface,
                        'label': f'{iface} {iface_to_ip.get(iface, "")}'.strip() if iface_to_ip.get(iface) else iface,
                        'data': [None] * len(labels),
                    }
                    for iface in seen_ifaces
                ]
                net_data = NetworkMetric.objects.filter(
                    rig_uuid=str(uuid),
                    timestamp__gte=start_bucket,
                    timestamp__lte=end_bucket,
                ).order_by('timestamp')[:50000]
                self._fill_buckets_multi_key(labels, iface_datasets, start_bucket, net_data, field_name, 'interface', bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
                datasets = iface_datasets
                # Convert byte deltas to MB/s for network metrics
                if field_name in self.BYTE_TO_MB:
                    for ds in datasets:
                        ds['data'] = [round(v / (1024 * 1024), 2) if v is not None else None for v in ds['data']]
            else:
                net_data = NetworkMetric.objects.filter(
                    rig_uuid=str(uuid),
                    interface__isnull=False,
                    timestamp__gte=start_bucket,
                    timestamp__lte=end_bucket,
                ).order_by('timestamp')[:10000]
                self._fill_buckets(labels, values, start_bucket, net_data, field_name, bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
                datasets = [{'label': metric, 'data': values}]
                if field_name in self.BYTE_TO_MB:
                    datasets[0]['data'] = [round(v / (1024 * 1024), 2) if v is not None else None for v in datasets[0]['data']]

        elif metric.startswith('container_'):
            # Multi-container metrics: cpu_pct, mem_usage_bytes, restart_count
            CONTAINER_FIELD_MAP = {
                'container_cpu_pct': 'cpu_pct',
                'container_mem_usage_bytes': 'mem_usage_bytes',
                'container_restart_count': 'restart_count',
            }
            if metric not in CONTAINER_FIELD_MAP:
                return Response({'status': 'error', 'message': f'Unknown container metric: {metric}'}, status=400)
            field_name = CONTAINER_FIELD_MAP[metric]
            container_info = (
                DockerContainerMetric.objects.filter(
                    rig_uuid=str(uuid),
                    timestamp__gte=start_bucket,
                    timestamp__lte=end_bucket,
                )
                .values('name')
                .distinct()
                .order_by('name')
            )
            seen_containers = [row['name'] for row in container_info if row['name']]
            container_datasets = [
                {'_key': name, 'label': name, 'data': [None] * len(labels)}
                for name in seen_containers
            ]
            container_data = DockerContainerMetric.objects.filter(
                rig_uuid=str(uuid),
                timestamp__gte=start_bucket,
                timestamp__lte=end_bucket,
            ).order_by('timestamp')[:50000]
            self._fill_buckets_multi_key(labels, container_datasets, start_bucket, container_data, field_name, 'name', bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
            datasets = container_datasets
            if field_name == 'mem_usage_bytes':
                for ds in datasets:
                    ds['data'] = [round(v / (1024**3), 2) if v is not None else None for v in ds['data']]

        elif metric.startswith('ai_'):
            AI_FIELD_MAP = {
                'ai_gpu_mem_mb': 'gpu_mem_used_mb',
                'ai_cpu_pct': 'cpu_pct',
            }
            if metric not in AI_FIELD_MAP:
                return Response({'status': 'error', 'message': f'Unknown AI metric: {metric}'}, status=400)
            field_name = AI_FIELD_MAP[metric]
            ai_info = (
                AIProcessMetric.objects.filter(
                    rig_uuid=str(uuid),
                    timestamp__gte=start_bucket,
                    timestamp__lte=end_bucket,
                )
                .values('process_name')
                .distinct()
                .order_by('process_name')
            )
            seen_procs = [row['process_name'] for row in ai_info if row['process_name']]
            ai_datasets = [
                {'_key': name, 'label': name, 'data': [None] * len(labels)}
                for name in seen_procs
            ]
            ai_data = AIProcessMetric.objects.filter(
                rig_uuid=str(uuid),
                timestamp__gte=start_bucket,
                timestamp__lte=end_bucket,
            ).order_by('timestamp')[:50000]
            self._fill_buckets_multi_key(labels, ai_datasets, start_bucket, ai_data, field_name, 'process_name', bucket_minutes=self._bucket_minutes, aggregate=self._aggregate)
            datasets = ai_datasets

        elif metric == 'error_frequency':
            occurrences = ErrorEventOccurrence.objects.filter(
                rig_uuid=str(uuid),
                timestamp__gte=start_bucket,
                timestamp__lte=end_bucket,
            ).order_by('timestamp')[:50000]
            total_buckets = len(labels)
            error_counts = [0] * total_buckets
            bucket_seconds = bucket_minutes * 60
            for occ in occurrences:
                ts = occ.timestamp.replace(second=0, microsecond=0)
                delta = ts - start_bucket
                idx = int(delta.total_seconds() // bucket_seconds)
                if 0 <= idx < total_buckets:
                    error_counts[idx] += 1
            # For aggregated buckets, label shows the bucket size
            if bucket_minutes == 1:
                label = 'Errors/min'
            elif bucket_minutes == 15:
                label = 'Errors/15min'
            else:
                label = 'Errors/hour'
            datasets = [{'label': label, 'data': error_counts}]

        else:
            return Response({'status': 'error', 'message': f'Unknown metric: {metric}'}, status=400)

        return Response({
            'labels': labels,
            'datasets': datasets,
        })
