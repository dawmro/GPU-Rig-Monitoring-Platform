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
from django.db.models import Avg, Sum, F
from django.db.models.functions import TruncMinute, TruncHour

from accounts.authentication import APIKeyAuthentication
from accounts.models import ApiKey
from .serializers import process_ingest
from .models import LatestSnapshot, MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, PowerReading
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
        enrolled_by_key_changed = rig.enrolled_by_api_key_id != api_key.id
        if enrolled_by_key_changed:
            rig.enrolled_by_api_key = api_key

        # Process the payload
        result, http_status = process_ingest(rig_uuid, data, user.id, rig=rig, enrolled_by_key_changed=enrolled_by_key_changed)

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


class ChartRateThrottle(SimpleRateThrottle):
    """Rate limit for chart data endpoint — 120 requests per minute per user.

    A page load fires ~18 chart requests. We allow burst to avoid throttling
    legitimate page loads while still preventing abuse.
    """
    scope = 'chart_data'

    def get_cache_key(self, request, view):
        if request.user.is_authenticated:
            return f'chart_rl_user_{request.user.id}'
        return f'chart_rl_ip_{request.META.get("REMOTE_ADDR", "unknown")}'


class ChartDataView(APIView):
    """GET /api/v1/rigs/<uuid>/chart-data/ — Historical chart data.

    Optimization strategy: The ``compact_data`` management command
    pre-buckets time-series data on a daily schedule:

    - **0-1 day**:  raw 1-minute data  (tier 1)
    - **1-7 days**: pre-bucketed 15-min (tier 2)
    - **7-31 days**: pre-bucketed 1-hour (tier 3)

    This view exploits that pre-bucketing: instead of re-aggregating on
    every request, it directly reads the rows that ``compact_data`` already
    produced. The bucket size for the query matches the data's granularity:

    - range ≤ 24h  → 1-min raw rows
    - range ≤ 168h → 15-min pre-bucketed rows
    - range > 168h → 1-hour pre-bucketed rows

    ``MetricSnapshot`` is NOT compacted by ``compact_data``, so for
    CPU/Memory/Power charts we still need to aggregate on the fly. The
    aggregation uses SQL (not Python loops) and only the minimum fields
    are selected.

    For multi-device queries (multi_gpu, multi_disk, multi_iface) we use
    a single GROUP BY query and pivot in Python — eliminating the prior
    N+1 pattern.
    """
    authentication_classes = [SessionAuthentication]
    throttle_classes = [ChartRateThrottle]

    # Metrics that live in MetricSnapshot (CPU/Memory/Power) — require
    # on-the-fly aggregation since this table is not compacted.
    SNAPSHOT_METRICS = frozenset({
        'cpu_utilization_pct', 'cpu_temp_c', 'cpu_freq_current_mhz',
        'mem_total_bytes', 'mem_used_bytes', 'mem_free_bytes', 'mem_cached_bytes',
        'swap_used_bytes', 'swap_total_bytes',
        'cpu_power_w', 'total_system_power_w',
    })

    # Map chart metric -> GPUMetric DB column.
    GPU_METRICS = {
        'gpu_temp_c': 'gpu_temp_c',
        'gpu_util_pct': 'gpu_util_pct',
        'gpu_mem_controller_util_pct': 'mem_controller_util_pct',
        'gpu_mem_used_mb': 'mem_used_mb',
        'gpu_mem_total_mb': 'mem_total_mb',
        'gpu_power_w': 'power_draw_w',
        'gpu_power_limit_w': 'power_limit_w',
        'gpu_fan_pct': 'fan_speed_pct',
        'gpu_core_clock_mhz': 'gpu_core_clock_mhz',
        'gpu_mem_clock_mhz': 'gpu_mem_clock_mhz',
    }

    STORAGE_METRICS = frozenset({'disk_usage_pct'})
    DISK_IO_METRICS = {
        'disk_read_bytes_delta': 'read_bytes_delta',
        'disk_write_bytes_delta': 'write_bytes_delta',
        'disk_read_iops_delta': 'read_iops_delta',
        'disk_write_iops_delta': 'write_iops_delta',
        'disk_utilization_pct': 'utilization_pct',
    }
    DISK_BYTE_METRICS = frozenset({'disk_read_bytes_delta', 'disk_write_bytes_delta'})

    # Map chart metric -> NetworkMetric DB column.
    NETWORK_METRICS = {
        'net_rx_bytes_delta': 'rx_bytes_delta',
        'net_tx_bytes_delta': 'tx_bytes_delta',
        'net_rx_errors': 'rx_errors',
        'net_tx_errors': 'tx_errors',
    }

    # Metrics that should be summed, not averaged, across the bucket window.
    SUM_METRICS = frozenset({
        'net_rx_bytes_delta', 'net_tx_bytes_delta',
        'net_rx_errors', 'net_tx_errors',
        'error_frequency',
        'disk_read_bytes_delta', 'disk_write_bytes_delta',
        'disk_read_iops_delta', 'disk_write_iops_delta',
    })

    BYTE_TO_GB = frozenset({
        'mem_total_bytes', 'mem_used_bytes', 'mem_free_bytes',
        'mem_cached_bytes', 'swap_used_bytes', 'swap_total_bytes',
    })
    BYTE_TO_MB = frozenset({'rx_bytes_delta', 'tx_bytes_delta'})

    @staticmethod
    def _bucket_minutes_for_range(range_hours):
        """Map range_hours to bucket_minutes matching the compaction tier.

        Kept in sync with compact_data.py tier boundaries (1d, 7d, 31d)
        and with serializers.py:_chart_bucket_minutes() used at ingest
        time for cache invalidation.
        """
        if range_hours <= 24:
            return 1
        if range_hours <= 168:
            return 15
        return 60

    def _build_buckets(self, range_hours, bucket_minutes=1):
        """Build bucket boundaries aligned to the same boundaries as compaction script.

        For 15-minute buckets: align to hour boundaries (0, 15, 30, 45)
        For 1-hour buckets: align to hour boundaries
        For 1-minute buckets: align to minute boundaries
        """
        now = timezone.now()
        # Align end_bucket to the same boundary as compaction script
        end_bucket = now.replace(second=0, microsecond=0)
        if bucket_minutes == 60:
            end_bucket = end_bucket.replace(minute=0)
        elif bucket_minutes == 15:
            # Align to 15-minute boundary (0, 15, 30, 45)
            minute = (now.minute // 15) * 15
            end_bucket = end_bucket.replace(minute=minute)
        elif bucket_minutes == 1:
            pass  # Already aligned to minute

        total_buckets = (range_hours * 60) // bucket_minutes
        start_bucket = end_bucket - timedelta(minutes=total_buckets * bucket_minutes)
        labels = []
        for i in range(total_buckets):
            t = start_bucket + timedelta(minutes=i * bucket_minutes)
            if range_hours > 24 or bucket_minutes >= 60:
                labels.append(t.strftime('%m-%d %H:%M'))
            else:
                labels.append(t.strftime('%H:%M'))
        return labels, start_bucket, end_bucket

    def get(self, request, uuid):
        from django.core.cache import cache

        user = request.user
        rig = get_object_or_404(Rig, uuid=uuid)
        if rig.owner_id != user.id and not request.user.is_staff:
            return Response({'status': 'error', 'message': 'Forbidden'}, status=403)

        metric = request.query_params.get('metric', 'cpu_utilization_pct')
        range_hours = int(request.query_params.get('range', 24))
        bucket_minutes = self._bucket_minutes_for_range(range_hours)

        gpu_index = int(request.query_params.get('gpu_index', 0))
        multi_gpu = request.query_params.get('multi_gpu', 'false').lower() == 'true'
        multi_disk = request.query_params.get('multi_disk', 'false').lower() == 'true'
        multi_iface = request.query_params.get('multi_iface', 'false').lower() == 'true'
        multi_mem = request.query_params.get('multi_mem', 'false').lower() == 'true'

        # Cache key: chart_data_{uuid}_{metric}_{range}_{bucket}_{multi_flags}_{gpu_index}
        # TTL: 55s (just under the 60s agent heartbeat interval)
        # multi_* flags and gpu_index must be in the cache key to avoid
        # serving a single-dataset response to a multi-disk request (and vice versa)
        cache_key = (
            f'chart_{uuid}_{metric}_{range_hours}_{bucket_minutes}'
            f'_g{gpu_index}_{int(multi_gpu)}{int(multi_disk)}'
            f'{int(multi_iface)}{int(multi_mem)}'
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        labels, start_bucket, end_bucket = self._build_buckets(range_hours, bucket_minutes)
        total_buckets = len(labels)

        # ---- Dispatch ----
        if metric in self.SNAPSHOT_METRICS or metric in {'cpu_load_avg', 'uptime_s', 'error_frequency'}:
            response_data = self._handle_snapshot_metric(
                metric, uuid, start_bucket, end_bucket, total_buckets, labels, multi_mem
            )
        elif metric in self.GPU_METRICS:
            response_data = self._handle_gpu_metric(
                metric, uuid, start_bucket, end_bucket, total_buckets, labels,
                gpu_index, multi_gpu, bucket_minutes
            )
        elif metric in self.STORAGE_METRICS or metric in self.DISK_IO_METRICS:
            response_data = self._handle_storage_metric(
                metric, uuid, start_bucket, end_bucket, total_buckets, labels, multi_disk, bucket_minutes
            )
        elif metric in self.NETWORK_METRICS:
            response_data = self._handle_network_metric(
                metric, uuid, start_bucket, end_bucket, total_buckets, labels, multi_iface, bucket_minutes
            )
        else:
            return Response({'status': 'error', 'message': f'Unknown metric: {metric}'}, status=400)

        # Cache for 55s — just under the 60s agent heartbeat interval
        cache.set(cache_key, response_data, 55)
        return Response(response_data)

    # ------------------------------------------------------------------
    # Helpers for each metric family
    # ------------------------------------------------------------------

    def _trunc_for_bucket(self, bucket_minutes):
        """Return a SQL function that truncates timestamps to bucket boundaries.

        For 1-min: TruncMinute (already exists in Django).
        For 15-min: a custom Func that aligns to 0/15/30/45 minute marks
                    (matching compact_data tier 2).
        For 60-min: TruncHour.
        """
        if bucket_minutes == 1:
            return TruncMinute
        if bucket_minutes == 15:
            from django.db.models import Func, DateTimeField
            class Trunc15Min(Func):
                function = 'date_trunc'
                template = ("date_trunc('hour', %(expressions)s) + INTERVAL '15 min' "
                            "* (EXTRACT(MINUTE FROM %(expressions)s)::int / 15)")
                output_field = DateTimeField()
            return Trunc15Min
        return TruncHour

    def _bucket_index(self, ts, start_bucket, bucket_seconds):
        """Return the integer bucket index for a timestamp, or None if out of range."""
        delta = (ts - start_bucket).total_seconds()
        if delta < 0:
            return None
        idx = int(delta // bucket_seconds)
        return idx

    def _read_prebucketed(self, model, uuid, db_field, start_bucket, end_bucket,
                           bucket_minutes, group_by_keys, agg_func, agg_alias='val'):
        """Read data at the chart's bucket granularity using SQL aggregation.

        Works correctly with BOTH pre-bucketed and raw 1-min data:

        - For tier 2/3 data (already pre-bucketed at 15-min or 1-hour
          granularity), the GROUP BY collapses to 1 row per bucket, so
          AVG/SUM returns the bucket's stored value.
        - For tier 1 data (raw 1-min), the GROUP BY aggregates multiple
          rows per bucket into the bucket's average/sum.

        The bucket boundary SQL must match the compaction script's tier
        boundaries exactly so that pre-bucketed rows align with the
        chart's bucket array indices.

        Args:
            model: Django model class.
            uuid: Rig UUID.
            db_field: Database column to read.
            start_bucket, end_bucket: Query time range.
            bucket_minutes: Chart bucket size (1, 15, or 60).
            group_by_keys: List of columns to GROUP BY (e.g. ['gpu_index']).
            agg_func: 'avg' or 'sum'.
            agg_alias: Alias for the value column in the query.

        Returns:
            Dict mapping (group_key_tuple) -> {bucket_index: value}.
            e.g. {(0,): {0: v0, 1: v1, ...}, (1,): {0: v0, 1: v1, ...}}
        """
        bucket_seconds = bucket_minutes * 60

        # SQL aggregation always works because:
        # - For pre-bucketed rows: GROUP BY collapses to 1 row per bucket
        #   (since timestamp is already at the bucket boundary)
        # - For raw 1-min rows: GROUP BY aggregates multiple rows into 1
        trunc = self._trunc_for_bucket(bucket_minutes)
        qs = (model.objects
              .filter(rig_uuid=uuid, timestamp__gte=start_bucket,
                      timestamp__lte=end_bucket)
              .annotate(bucket=trunc('timestamp')))
        agg_expr = Sum(db_field) if agg_func == 'sum' else Avg(db_field)
        rows = (qs.values(*group_by_keys, 'bucket')
                 .annotate(**{agg_alias: agg_expr})
                 .order_by('bucket'))

        result = {}
        for row in rows:
            key = tuple(row[k] for k in group_by_keys)
            idx = self._bucket_index(row['bucket'], start_bucket, bucket_seconds)
            if idx is None:
                continue
            value = row[agg_alias]
            if value is None:
                continue
            if key not in result:
                result[key] = {}
            result[key][idx] = round(float(value), 2)
        return result

    def _arrays_from_groups(self, groups_dict, total_buckets):
        """Convert dict-of-buckets into dense list-of-Nones-per-key."""
        return [
            [g.get(i) for i in range(total_buckets)]
            for g in groups_dict.values()
        ]

    # ------------------------------------------------------------------
    # Metric family handlers
    # ------------------------------------------------------------------

    def _handle_snapshot_metric(self, metric, uuid, start_bucket, end_bucket,
                                 total_buckets, labels, multi_mem):
        """Handle MetricSnapshot metrics (CPU/Memory/Power/Load/Uptime/Errors).

        MetricSnapshot is NOT compacted, so we always aggregate on the fly.
        For multi_mem, we use a single GROUP BY query to avoid N+1.
        """
        bucket_minutes = self._bucket_minutes_for_range(
            int((end_bucket - start_bucket).total_seconds() / 3600) or 24
        )
        trunc = self._trunc_for_bucket(bucket_minutes)
        bucket_seconds = bucket_minutes * 60

        base_qs = MetricSnapshot.objects.filter(
            rig_uuid=uuid,
            timestamp__gte=start_bucket,
            timestamp__lte=end_bucket,
        )

        if metric == 'cpu_load_avg':
            # Special: load_avg is a JSON list of 3 values (1m, 5m, 15m).
            # Single query selecting the JSON, pivot in Python.
            rows = base_qs.annotate(bucket=trunc('timestamp')).values(
                'bucket', 'cpu_load_avg_json'
            ).order_by('bucket')
            load_datasets = [
                {'label': f'Load {m}m', 'data': [None] * total_buckets}
                for m in (1, 5, 15)
            ]
            for row in rows:
                idx = self._bucket_index(row['bucket'], start_bucket, bucket_seconds)
                if idx is None or idx >= total_buckets:
                    continue
                vals = row['cpu_load_avg_json']
                if not vals:
                    continue
                for i in range(min(3, len(vals))):
                    load_datasets[i]['data'][idx] = vals[i]
            return {'labels': labels, 'datasets': load_datasets}

        if metric == 'uptime_s':
            rows = base_qs.annotate(bucket=trunc('timestamp')).values(
                'bucket', 'uptime_s'
            ).order_by('bucket')
            values = [None] * total_buckets
            for row in rows:
                idx = self._bucket_index(row['bucket'], start_bucket, bucket_seconds)
                if idx is None or idx >= total_buckets or row['uptime_s'] is None:
                    continue
                values[idx] = round(row['uptime_s'] / 86400, 2)
            return {'labels': labels, 'datasets': [
                {'label': 'Uptime (days)', 'data': values}
            ]}

        if metric == 'error_frequency':
            rows = base_qs.annotate(bucket=trunc('timestamp')).values(
                'bucket'
            ).annotate(errors=Sum('error_count')).order_by('bucket')
            values = [0] * total_buckets
            for row in rows:
                idx = self._bucket_index(row['bucket'], start_bucket, bucket_seconds)
                if idx is None or idx >= total_buckets:
                    continue
                values[idx] = row['errors'] or 0
            label = 'Errors/min' if bucket_minutes == 1 else 'Errors/hour'
            return {'labels': labels, 'datasets': [
                {'label': label, 'data': values}
            ]}

        if multi_mem:
            mem_fields = (
                ('mem_used_bytes', 'Memory Used'),
                ('mem_free_bytes', 'Memory Free'),
                ('swap_used_bytes', 'Swap Used'),
            )
            # Single GROUP BY query (all three fields, one bucket each)
            rows = base_qs.annotate(bucket=trunc('timestamp')).values(
                'bucket'
            ).annotate(
                used=Avg('mem_used_bytes'),
                free=Avg('mem_free_bytes'),
                swap=Avg('swap_used_bytes'),
            ).order_by('bucket')
            datasets = [{'label': label, 'data': [None] * total_buckets}
                        for _, label in mem_fields]
            for row in rows:
                idx = self._bucket_index(row['bucket'], start_bucket, bucket_seconds)
                if idx is None or idx >= total_buckets:
                    continue
                datasets[0]['data'][idx] = round(row['used'] / (1024 ** 3), 2) if row['used'] is not None else None
                datasets[1]['data'][idx] = round(row['free'] / (1024 ** 3), 2) if row['free'] is not None else None
                datasets[2]['data'][idx] = round(row['swap'] / (1024 ** 3), 2) if row['swap'] is not None else None
            return {'labels': labels, 'datasets': datasets}

        # Single metric from MetricSnapshot
        agg = Avg(metric)
        rows = base_qs.annotate(bucket=trunc('timestamp')).values(
            'bucket'
        ).annotate(val=agg).order_by('bucket')
        values = [None] * total_buckets
        for row in rows:
            idx = self._bucket_index(row['bucket'], start_bucket, bucket_seconds)
            if idx is None or idx >= total_buckets:
                continue
            v = row['val']
            if v is None:
                continue
            values[idx] = round(v / (1024 ** 3), 2) if metric in self.BYTE_TO_GB else round(v, 2)
        return {'labels': labels, 'datasets': [
            {'label': metric, 'data': values}
        ]}

    def _handle_gpu_metric(self, metric, uuid, start_bucket, end_bucket,
                            total_buckets, labels, gpu_index, multi_gpu, bucket_minutes):
        """Handle GPU metrics. Uses pre-bucketed data from compact_data."""
        db_field = self.GPU_METRICS[metric]
        agg_func = 'sum' if metric in self.SUM_METRICS else 'avg'

        if not multi_gpu:
            # Single GPU, single GROUP BY query (no N+1)
            groups = self._read_prebucketed(
                GPUMetric, uuid, db_field, start_bucket, end_bucket,
                bucket_minutes, group_by_keys=['gpu_index'],
                agg_func=agg_func,
            )
            # Find the requested gpu_index (default 0)
            values = [None] * total_buckets
            key = (gpu_index,)
            if key in groups:
                for i, v in groups[key].items():
                    if i < total_buckets:
                        values[i] = v
            return {'labels': labels, 'datasets': [
                {'label': f'GPU {gpu_index}', 'data': values}
            ]}

        # multi_gpu: single GROUP BY (gpu_index, bucket) — no N+1
        groups = self._read_prebucketed(
            GPUMetric, uuid, db_field, start_bucket, end_bucket,
            bucket_minutes, group_by_keys=['gpu_index'],
            agg_func=agg_func,
        )
        # Sort by gpu_index
        sorted_keys = sorted(groups.keys(), key=lambda k: k[0])
        datasets = []
        for key in sorted_keys:
            values = [None] * total_buckets
            for i, v in groups[key].items():
                if i < total_buckets:
                    values[i] = v
            datasets.append({
                'label': f'GPU{key[0]}',
                'data': values,
            })
        return {'labels': labels, 'datasets': datasets}

    def _handle_storage_metric(self, metric, uuid, start_bucket, end_bucket,
                                total_buckets, labels, multi_disk, bucket_minutes):
        """Handle storage/disk metrics. Uses pre-bucketed data from compact_data.

        Args:
            bucket_minutes: Chart's bucket size (1, 15, or 60). Passed from
                the parent view — matches the chart's x-axis resolution
                and aligns with the compaction tier boundaries.
        """
        if metric in self.DISK_IO_METRICS:
            db_field = self.DISK_IO_METRICS[metric]
            agg_func = 'sum' if metric in self.SUM_METRICS else 'avg'
            byte_metric = metric in self.DISK_BYTE_METRICS
        else:
            db_field = 'usage_pct'
            agg_func = 'avg'
            byte_metric = False

        if not multi_disk:
            groups = self._read_prebucketed(
                StorageMetric, uuid, db_field, start_bucket, end_bucket,
                bucket_minutes=bucket_minutes,
                group_by_keys=['device'],
                agg_func=agg_func,
            )
            # Single disk or default to first
            values = [None] * total_buckets
            if groups:
                # Use first device alphabetically
                first_key = sorted(groups.keys())[0]
                for i, v in groups[first_key].items():
                    if i < total_buckets:
                        values[i] = v
            label_map = {
                'disk_read_bytes_delta': 'Read MB',
                'disk_write_bytes_delta': 'Write MB',
                'disk_read_iops_delta': 'Read IOPS',
                'disk_write_iops_delta': 'Write IOPS',
                'disk_utilization_pct': 'Utilization %',
            }
            label = label_map.get(metric, 'Disk Usage %')
            if byte_metric:
                values = [round((v or 0) / (1024 * 1024), 2) if v is not None else None
                          for v in values]
            return {'labels': labels, 'datasets': [
                {'label': label, 'data': values}
            ]}

        # multi_disk: single GROUP BY (device, bucket) — no N+1
        groups = self._read_prebucketed(
            StorageMetric, uuid, db_field, start_bucket, end_bucket,
            bucket_minutes=bucket_minutes,
            group_by_keys=['device'],
            agg_func=agg_func,
        )
        sorted_keys = sorted(groups.keys(), key=lambda k: k[0] or '')
        datasets = []
        for key in sorted_keys:
            values = [None] * total_buckets
            for i, v in groups[key].items():
                if i < total_buckets:
                    values[i] = v
            if byte_metric:
                values = [round((v or 0) / (1024 * 1024), 2) if v is not None else None
                          for v in values]
            datasets.append({'label': key[0] or 'Unknown', 'data': values})
        return {'labels': labels, 'datasets': datasets}

    def _handle_network_metric(self, metric, uuid, start_bucket, end_bucket,
                                total_buckets, labels, multi_iface, bucket_minutes):
        """Handle network metrics. Uses pre-bucketed data from compact_data.

        Args:
            bucket_minutes: Chart's bucket size (1, 15, or 60). Passed from
                the parent view — matches the chart's x-axis resolution
                and aligns with the compaction tier boundaries.
        """
        db_field = self.NETWORK_METRICS[metric]
        agg_func = 'sum' if metric in self.SUM_METRICS else 'avg'
        byte_metric = db_field in self.BYTE_TO_MB

        if not multi_iface:
            groups = self._read_prebucketed(
                NetworkMetric, uuid, db_field, start_bucket, end_bucket,
                bucket_minutes=bucket_minutes,
                group_by_keys=['interface'],
                agg_func=agg_func,
            )
            values = [None] * total_buckets
            if groups:
                first_key = sorted(groups.keys())[0]
                for i, v in groups[first_key].items():
                    if i < total_buckets:
                        values[i] = v
            if byte_metric:
                values = [round((v or 0) / (1024 * 1024), 2) if v is not None else None
                          for v in values]
            return {'labels': labels, 'datasets': [
                {'label': metric, 'data': values}
            ]}

        # multi_iface: single GROUP BY (interface, bucket) — no N+1
        groups = self._read_prebucketed(
            NetworkMetric, uuid, db_field, start_bucket, end_bucket,
            bucket_minutes=bucket_minutes,
            group_by_keys=['interface'],
            agg_func=agg_func,
        )
        sorted_keys = sorted(groups.keys(), key=lambda k: k[0] or '')
        datasets = []
        for key in sorted_keys:
            values = [None] * total_buckets
            for i, v in groups[key].items():
                if i < total_buckets:
                    values[i] = v
            if byte_metric:
                values = [round((v or 0) / (1024 * 1024), 2) if v is not None else None
                          for v in values]
            datasets.append({'label': key[0] or 'Unknown', 'data': values})
        return {'labels': labels, 'datasets': datasets}