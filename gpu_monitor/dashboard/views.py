from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_POST
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from functools import wraps
import re
import time
from collections import Counter
from types import SimpleNamespace

from rigs.models import Rig, RigTag
from metrics_app.models import MetricSnapshot, LatestSnapshot, GPUMetric, GPUProcessMetric, StorageMetric, NetworkMetric, LatestDockerContainer
from audit.middleware import log_audit_event

# Pre-compiled regex for natural sort: splits "rig12" -> ["rig", 12, ""]
_NATURAL_SORT_RE = re.compile(r'(\d+)')

# Cache TTLs for Rig-related cached data (seconds).
# 30s for high-frequency polls (htmx_metrics, htmx_rig_status).
# After a rig's name/tags/owner changes, max 30s staleness.
# For permission-sensitive operations, we re-validate from DB.
_RIG_CACHE_TTL_S = 30


def _get_rig_light_cached(uuid, user):
    """Get a minimal Rig representation for permission check + status display.

    Returns a SimpleNamespace with: uuid, owner_id, status, last_seen
    (or None if not found / not accessible to user).

    Used by high-frequency HTMX endpoints (htmx_metrics, htmx_rig_status)
    that poll every 15-30s. Avoids 1 DB query per poll.

    Full Rig object is NOT cached here because:
    - It's heavy (tags, owner, GPU arrays)
    - Permission check only needs owner_id
    - Status display only needs .status + .last_seen
    - For full data (rig_detail), use Rig.objects.get() directly

    Returns None if rig doesn't exist OR user doesn't have access.
    """
    cache_key = f'rig_light_{uuid}'
    cached = cache.get(cache_key)
    if cached is not None:
        rig = cached
    else:
        # Minimal DB query — only fields we need
        try:
            row = Rig.objects.only('uuid', 'owner_id', 'status', 'last_seen').get(uuid=uuid)
        except Rig.DoesNotExist:
            return None
        rig = SimpleNamespace(
            uuid=row.uuid,
            owner_id=row.owner_id,
            status=row.status,
            last_seen=row.last_seen,
        )
        cache.set(cache_key, rig, _RIG_CACHE_TTL_S)

    # Permission check (always re-validate from cached data, not DB)
    if rig.owner_id != user.id and not user.is_staff:
        return None
    return rig


def invalidate_rig_cache(uuid):
    """Invalidate cached Rig data. Call this when rig data changes
    (rename, ownership transfer, status change, tag changes, delete).

    Safe to call even if no cache entry exists.
    """
    cache.delete(f'rig_light_{uuid}')
    # Also invalidate the LatestSnapshot cache (in case it's affected)
    cache.delete(f'lsnap_{uuid}')


def index_view(request):
    """Root URL landing page.

    Authenticated users are redirected to the dashboard (rig list).
    Unauthenticated users are redirected to the login page.
    """
    if request.user.is_authenticated:
        return redirect('dashboard:rig-list')
    return redirect('accounts:login')


def rate_limit(max_requests, window_s):
    """Simple per-user/IP rate limit decorator for Django views.

    Args:
        max_requests: Maximum number of requests allowed in the window.
        window_s: Time window in seconds.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Use user ID for authenticated users, IP for anonymous
            if request.user.is_authenticated:
                key = f'rl_user_{request.user.id}'
            else:
                key = f'rl_ip_{request.META.get("REMOTE_ADDR", "unknown")}'

            now = time.time()
            window_start = now - window_s

            # Get request timestamps from cache
            timestamps = cache.get(key, [])
            # Remove timestamps outside the current window
            timestamps = [t for t in timestamps if t > window_start]

            if len(timestamps) >= max_requests:
                return HttpResponse(
                    'Rate limit exceeded. Please slow down.',
                    status=429,
                    content_type='text/plain'
                )

            timestamps.append(now)
            cache.set(key, timestamps, timeout=window_s)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _json_get(lst, idx, default=None):
    """Safely get an element from a JSON array field."""
    if lst and idx < len(lst):
        return lst[idx]
    return default


def _build_gpu_title(values, default_value='N/A', suffix='', fmt=None):
    """Build a 'GPU1: X | GPU2: Y | ...' title string for tooltips.

    Used in _rig_table.html to pre-compute title attributes for multi-GPU
    cells. Without this, the template iterates the JSON list per row to
    build the title, causing O(rigs × gpus) Python iterations on every
    Fleet Overview page load.

    Args:
        values: List of values (e.g. ['RTX 4090', 'A100', None])
                or None if rig has no GPUs
        default_value: Value to use when entry is None (e.g. 'Unknown', 'N/A')
        suffix: String to append to formatted value (e.g. '°C', '%', ' MHz')
        fmt: Optional format spec (e.g. '.1f' for 1 decimal place)

    Returns:
        Formatted title string like 'GPU1: RTX 4090 | GPU2: A100 | GPU3: N/A'
        or empty string if values is None/empty.
    """
    if not values:
        return ''

    parts = []
    for i, v in enumerate(values, 1):
        if v is None:
            value_str = default_value
        elif fmt:
            value_str = f'{v:{fmt}}{suffix}'
        else:
            value_str = f'{v}{suffix}'
        parts.append(f'GPU{i}: {value_str}')

    return ' | '.join(parts)


def _fetch_rig_metrics(uuid, rig=None):
    """Fetch the latest rig metrics for Live Metrics display.

    Uses SQL-level latest-per-device queries instead of fetching all rows.
    """
    # LatestSnapshot changes only on heartbeat (~60s), but is polled every 30s.
    # Cache with 50s TTL to reduce DB reads between heartbeats.
    snapshot = None
    if rig is not None:
        cache_key = f'lsnap_{rig.uuid}'
        snapshot = cache.get(cache_key)
        if snapshot is None:
            try:
                snapshot = LatestSnapshot.objects.get(rig_uuid=str(uuid))
            except LatestSnapshot.DoesNotExist:
                pass
            else:
                cache.set(cache_key, snapshot, 50)

    # GPU data: read from LatestSnapshot JSON arrays instead of querying
    # the GPUMetric timeseries table. This avoids the expensive DISTINCT ON
    # query on 2.1M+ rows. Build a list of dicts matching the template's
    # expected format (mimicking GPUMetric objects).
    gpu_metrics = []
    if snapshot and snapshot.gpu_count:
        for i in range(snapshot.gpu_count):
            gpu_metrics.append({
                'gpu_index': i,
                'gpu_uuid': _json_get(snapshot.gpu_uuids_json, i, ''),
                'model': _json_get(snapshot.gpu_models_json, i, ''),
                'gpu_temp_c': _json_get(snapshot.gpu_temps_json, i),
                'gpu_util_pct': _json_get(snapshot.gpu_utils_json, i),
                'fan_speed_pct': _json_get(snapshot.gpu_fans_json, i),
                'gpu_core_clock_mhz': _json_get(snapshot.gpu_core_clocks_json, i),
                'gpu_mem_clock_mhz': _json_get(snapshot.gpu_mem_clocks_json, i),
                'mem_used_mb': _json_get(snapshot.gpu_mem_used_json, i),
                'mem_total_mb': _json_get(snapshot.gpu_mem_total_json, i),
                'mem_util_pct': _json_get(snapshot.gpu_mem_util_pcts_json, i),
                'mem_controller_util_pct': _json_get(snapshot.gpu_mem_controller_utils_json, i),
                'mem_free_mb': _json_get(snapshot.gpu_mem_free_json, i),
                'power_draw_w': _json_get(snapshot.gpu_power_draws_json, i),
                'power_limit_w': _json_get(snapshot.gpu_power_limits_json, i),
                'pcie_current_gen': _json_get(snapshot.gpu_pcie_gen_json, i),
                'pcie_max_gen': _json_get(snapshot.gpu_pcie_max_gen_json, i),
                'pcie_current_width': _json_get(snapshot.gpu_pcie_width_json, i),
                'pcie_max_width': _json_get(snapshot.gpu_pcie_max_width_json, i),
            })

    # Storage: read from LatestSnapshot JSON arrays instead of querying
    # the StorageMetric timeseries table. Build list of dicts matching
    # the template's expected format (mimicking StorageMetric objects).
    storage_metrics = []
    if snapshot and snapshot.storage_count:
        for i in range(snapshot.storage_count):
            storage_metrics.append({
                'device': _json_get(snapshot.storage_devices_json, i, ''),
                'fstype': _json_get(snapshot.storage_fstypes_json, i, ''),
                'mountpoint': _json_get(snapshot.storage_mountpoints_json, i, ''),
                'capacity_bytes': _json_get(snapshot.storage_capacities_json, i),
                'usage_pct': _json_get(snapshot.storage_usage_pcts_json, i),
                'temp_c': _json_get(snapshot.storage_temps_json, i),
                'smart_health': _json_get(snapshot.storage_smart_json, i, ''),
                # Disk I/O metrics — deltas (since last sample) and cumulative totals (since boot)
                'read_bytes_delta': _json_get(snapshot.storage_read_bytes_delta_json, i),
                'write_bytes_delta': _json_get(snapshot.storage_write_bytes_delta_json, i),
                'read_iops_delta': _json_get(snapshot.storage_read_iops_delta_json, i),
                'write_iops_delta': _json_get(snapshot.storage_write_iops_delta_json, i),
                'utilization_pct': _json_get(snapshot.storage_utilization_pcts_json, i),
                'read_bytes_total': _json_get(snapshot.storage_read_bytes_total_json, i),
                'write_bytes_total': _json_get(snapshot.storage_write_bytes_total_json, i),
                'read_iops_total': _json_get(snapshot.storage_read_iops_total_json, i),
                'write_iops_total': _json_get(snapshot.storage_write_iops_total_json, i),
            })

    # Network: read from LatestSnapshot JSON arrays instead of querying
    # the NetworkMetric timeseries table. Build list of dicts matching
    # the template's expected format (mimicking NetworkMetric objects).
    network_metrics = []
    if snapshot and snapshot.network_count:
        for i in range(snapshot.network_count):
            network_metrics.append({
                'interface': _json_get(snapshot.network_interfaces_json, i, ''),
                'ipv4': _json_get(snapshot.network_ipv4s_json, i, ''),
                'link_speed_mbps': _json_get(snapshot.network_speeds_json, i),
                'rx_bytes': _json_get(snapshot.network_rx_bytes_json, i),
                'tx_bytes': _json_get(snapshot.network_tx_bytes_json, i),
                'rx_errors': _json_get(snapshot.network_rx_errors_json, i, 0),
                'tx_errors': _json_get(snapshot.network_tx_errors_json, i, 0),
            })

    # Docker containers: LatestDockerContainer has all needed fields
    # Deduplicate by container_id at query level (defense-in-depth)
    latest_containers = (
        LatestDockerContainer.objects
        .filter(rig_uuid=str(uuid))
        .order_by('container_id')
        .distinct('container_id')
    )

    docker_metrics = []
    for lc in latest_containers:
        docker_metrics.append({
            'container_id': lc.container_id,
            'name': lc.name,
            'image': lc.image,
            'status': lc.status,
            'created': lc.created,
            'status_text': lc.status_text,
            'manifest': lc.manifest_json,
            'logs': lc.logs_json,
        })

    # Recent errors — last 10 from error_history (for Live Metrics card)
    error_history = rig.error_history_json if rig else []
    recent_errors = list(reversed(error_history[-10:])) if error_history else []

    # Rolling container history (for rig detail page)
    container_history = rig.container_history_json if rig else []

    # GPU processes: read from LatestSnapshot denormalized field
    # This is always the CURRENT snapshot's processes (not historical)
    # since the serializer deletes old GPUProcessMetric rows each heartbeat
    # AND the denormalized json in LatestSnapshot is overwritten in place.
    gpu_processes = snapshot.gpu_processes_json if snapshot else []

    # Derive primary IP from the first non-loopback, non-virtual interface
    # (for rig header display). Prefers physical NICs over virtual adapters.
    primary_ip = ''
    for iface in network_metrics:
        ip = iface.get('ipv4', '')
        if not ip or ip == '—':
            continue
        # Skip loopback
        if ip.startswith('127.'):
            continue
        # Skip common virtual adapter prefixes
        name = iface.get('interface', '').lower()
        if any(prefix in name for prefix in ('vmware', 'virtual', 'vbox', 'hyper-v', 'docker', 'tun', 'tap', 'br-', 'veth')):
            continue
        primary_ip = ip
        break
    # Fallback: if all interfaces were filtered, use the first non-loopback IP
    if not primary_ip:
        for iface in network_metrics:
            ip = iface.get('ipv4', '')
            if ip and ip != '—' and not ip.startswith('127.'):
                primary_ip = ip
                break

    # Process Details: union of top-10 by CPU and top-10 by memory,
    # deduplicated by PID, entries without a command line omitted
    # (kernel/system pseudo-processes), sorted by cpu_pct desc then
    # mem_pct desc. Rendered by the "Process Details" card
    # (_metrics_cards.html).
    seen_pids = set()
    process_details = []
    for proc in ((snapshot.top_cpu_processes_json if snapshot else [])[:10]
                 + (snapshot.top_mem_processes_json if snapshot else [])[:10]):
        pid = proc.get('pid')
        if pid in seen_pids:
            continue
        if not proc.get('cmdline'):
            continue  # omit kernel/system pseudo-processes without a command line
        seen_pids.add(pid)
        process_details.append({
            'pid': pid,
            'name': proc.get('name') or '—',
            'cmdline': proc.get('cmdline'),
            'cpu_pct': proc.get('cpu_pct') or 0.0,
            'mem_pct': proc.get('mem_pct') or 0.0,
        })
    process_details.sort(key=lambda p: (-p['cpu_pct'], -p['mem_pct']))

    return {
        'snapshot': snapshot,
        'gpu_metrics': gpu_metrics,
        'gpu_processes': gpu_processes,
        'storage_metrics': storage_metrics,
        'network_metrics': network_metrics,
        'docker_metrics': docker_metrics,
        'recent_errors': recent_errors,
        'error_history': error_history,
        'container_history': container_history,
        'primary_ip': primary_ip,
        'top_cpu_processes': snapshot.top_cpu_processes_json if snapshot else [],
        'top_mem_processes': snapshot.top_mem_processes_json if snapshot else [],
        'process_count': snapshot.process_count if snapshot else 0,
        'process_details': process_details,
    }


@login_required
@rate_limit(max_requests=60, window_s=60)
def rig_list(request):
    """Fleet Overview page.

    Refreshes every 30s via HTMX (see rig_list.html).
    All table columns are rendered from ``rig_data`` passed to
    ``_rig_table.html``.  To add a new column:

    1. Add the column header to ``_rig_table.html`` <thead>.
    2. Fetch the data here in the ``rig_data`` loop (or annotate the queryset).
    3. Extend the ``rig_data.append({...})`` dict with the new key.
    4. Add the <td> cell in ``_rig_table.html`` <tbody> using the new key.

    Query strategy:
    - 1 query: Rig base queryset (with prefetched tags + owner)
    - 1 query: LatestSnapshot batch fetch for all rig UUIDs
    - Counts derived in Python (no extra queries) via Counter
    - Total: 2 queries regardless of how many rigs
    """
    user = request.user

    # Step 1: Load ALL rigs (no status/search/tag filter) for status counts.
    # This avoids re-querying with .values_list().annotate(Count) which
    # was an extra DB roundtrip.
    if user.is_staff:
        all_rigs = Rig.objects.all().prefetch_related('tags', 'owner')
    else:
        all_rigs = Rig.objects.filter(owner=user).prefetch_related('tags', 'owner')

    # Step 2: Compute status counts from already-loaded data.
    # Counter is O(N) and avoids an extra .values_list('status').annotate() query.
    status_counts = dict(Counter(r.status for r in all_rigs))
    online_count = status_counts.get('online', 0)
    stale_count = status_counts.get('stale', 0)
    offline_count = status_counts.get('offline', 0)
    total_count = online_count + stale_count + offline_count

    # Step 3: Apply user filters to derive the displayed queryset.
    # We use the already-loaded all_rigs list for natural sort (Python-side)
    # since prefetched relations + Python sort is faster than re-fetching
    # ordered with .order_by() on the queryset.
    rigs = all_rigs

    status_filter = request.GET.get('status', '')
    if status_filter:
        rigs = [r for r in rigs if r.status == status_filter]

    search = request.GET.get('search', '')
    if search:
        rigs = [r for r in rigs if search.lower() in (r.name or '').lower()]

    tag_filter = request.GET.get('tag', '')
    if tag_filter:
        # Build set of rig UUIDs that have the requested tag (1 query)
        # instead of per-rig tags.filter().exists() which is N+1.
        rigs_with_tag = set(
            RigTag.objects.filter(name=tag_filter).values_list('rigs__uuid', flat=True)
        )
        rigs = [r for r in rigs if str(r.uuid) in rigs_with_tag]

    # Sort rigs naturally by name (e.g., rig2 before rig11).
    # Use pre-compiled _NATURAL_SORT_RE for efficiency.
    def _natural_sort_key(value):
        """Split string into text/number chunks for human-friendly sorting."""
        return [
            int(chunk) if chunk.isdigit() else chunk.lower()
            for chunk in _NATURAL_SORT_RE.split(value or '')
        ]
    rigs = sorted(rigs, key=lambda r: _natural_sort_key(r.name))

    # Batch-fetch all LatestSnapshot rows in ONE query (avoids N+1)
    rig_uuids = [str(r.uuid) for r in rigs]
    latest_snapshots = {
        str(s.rig_uuid): s  # Use str key to match rig_uuid_str lookups
        for s in LatestSnapshot.objects.filter(rig_uuid__in=rig_uuids)
    }

    # Build rig_data using snapshot data (no GPUMetric queries needed).
    # Pre-compute title strings for multi-GPU cells (4 cells per row × N rigs).
    # Without this, the template iterates the JSON list 4× per row to build
    # title attributes, causing O(rigs × gpus) Python iterations on every page load.
    rig_data = []
    for rig in rigs:
        rig_uuid_str = str(rig.uuid)
        snapshot = latest_snapshots.get(rig_uuid_str)
        rig_data.append({
            'rig': rig,
            'snapshot': snapshot,
            'gpu_models_title': _build_gpu_title(snapshot.gpu_models_json if snapshot else None,
                                                 default_value='Unknown', suffix=''),
            'gpu_temps_title': _build_gpu_title(snapshot.gpu_temps_json if snapshot else None,
                                                default_value='N/A', suffix='°C', fmt='.1f'),
            'gpu_fans_title': _build_gpu_title(snapshot.gpu_fans_json if snapshot else None,
                                               default_value='N/A', suffix='%', fmt='.0f'),
            'gpu_utils_title': _build_gpu_title(snapshot.gpu_utils_json if snapshot else None,
                                                default_value='N/A', suffix='%', fmt='.1f'),
        })

    if request.headers.get('HX-Request'):
        return render(request, 'dashboard/_rig_table.html', {'rig_data': rig_data})

    all_tags = RigTag.objects.filter(user=user).order_by('name') if not user.is_staff else RigTag.objects.all().order_by('name')

    return render(request, 'dashboard/rig_list.html', {
        'rig_data': rig_data,
        'status_filter': status_filter,
        'search': search,
        'tag_filter': tag_filter,
        'all_tags': all_tags,
        'online_count': online_count,
        'stale_count': stale_count,
        'offline_count': offline_count,
        'total_count': total_count,
    })


@login_required
def rig_toggle_tag(request, uuid, tag_id):
    """Toggle a tag on/off for a rig."""
    if request.method == 'POST':
        rig = get_object_or_404(Rig, uuid=uuid)
        if rig.owner_id != request.user.id and not request.user.is_staff:
            raise Http404
        tag = get_object_or_404(RigTag, id=tag_id, user=request.user)
        if tag in rig.tags.all():
            rig.tags.remove(tag)
            action = 'tag.removed'
        else:
            rig.tags.add(tag)
            action = 'tag.added'
        log_audit_event(request, action, 'Rig', rig.uuid, {'tag': tag.name})
        # Invalidate cached rig data (tags changed; status badge still valid
        # but the cached rig data may be stale for htmx_metrics in 30s)
        invalidate_rig_cache(uuid)
        if request.headers.get('HX-Request'):
            return render(request, 'dashboard/_rig_tags.html', {'rig': rig})
    return redirect('dashboard:rig-detail', uuid=uuid)


@login_required
@rate_limit(max_requests=60, window_s=60)
def rig_detail(request, uuid):
    """Rig detail page."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404

    context = _fetch_rig_metrics(uuid, rig)
    context['rig'] = rig
    context['is_data_stale'] = rig.status in [Rig.Status.OFFLINE, Rig.Status.STALE]

    return render(request, 'dashboard/rig_detail.html', context)


@login_required
@rate_limit(max_requests=120, window_s=60)
def htmx_metrics(request, uuid):
    """HTMX polling endpoint for live metrics.

    Polled every ~30s by the rig detail page. Uses cached Rig lookup to
    avoid a DB query per poll (was: 1 query every 30s per rig = 200 queries/min
    for 100 rigs). The 30s cache TTL matches typical rig data change frequency.
    """
    rig = _get_rig_light_cached(uuid, request.user)
    if rig is None:
        raise Http404

    # _fetch_rig_metrics accepts a rig argument. Our SimpleNamespace is compatible
    # with the duck-typed access (rig.uuid, rig.status, etc.).
    context = _fetch_rig_metrics(uuid, rig)
    # Template (_metrics_cards.html) only needs snapshot + is_data_stale,
    # but we pass rig for consistency with the rig_detail view.
    context['rig'] = rig
    context['is_data_stale'] = rig.status in [Rig.Status.OFFLINE, Rig.Status.STALE]

    return render(request, 'dashboard/_metrics_cards.html', context)


@login_required
@rate_limit(max_requests=120, window_s=60)
def htmx_rig_status(request, uuid):
    """HTMX polling endpoint — returns just the status badge + last_seen.

    Polled every ~15s for the Fleet Overview status badges. Uses cached
    Rig lookup (was: 4 queries/min × 100 rigs = 400 queries/min).
    """
    rig = _get_rig_light_cached(uuid, request.user)
    if rig is None:
        raise Http404

    # rig already has uuid, owner_id, status, last_seen from cache
    return render(request, 'dashboard/_rig_status_badge.html', {'rig': rig})


@login_required
@require_POST
def rig_delete(request, uuid):
    """Delete a rig and all its associated data."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404

    rig_name = rig.name

    # Delete all associated metric data (MetricSnapshot has rig_uuid as UUIDField, not FK)
    from metrics_app.models import MetricSnapshot, LatestSnapshot, GPUMetric, GPUProcessMetric, \
        StorageMetric, NetworkMetric, LatestDockerContainer, RigStatusEvent
    MetricSnapshot.objects.filter(rig_uuid=uuid).delete()
    LatestSnapshot.objects.filter(rig_uuid=uuid).delete()
    GPUMetric.objects.filter(rig_uuid=uuid).delete()
    GPUProcessMetric.objects.filter(rig_uuid=uuid).delete()
    StorageMetric.objects.filter(rig_uuid=uuid).delete()
    NetworkMetric.objects.filter(rig_uuid=uuid).delete()
    LatestDockerContainer.objects.filter(rig_uuid=uuid).delete()
    RigStatusEvent.objects.filter(rig_uuid=uuid).delete()

    rig.delete()
    # Invalidate all cached data for this rig (rig deleted, no longer exists)
    invalidate_rig_cache(uuid)
    log_audit_event(request, 'rig.deleted', 'Rig', uuid, {'name': rig_name})

    if request.headers.get('HX-Request'):
        response = render(request, 'dashboard/_rig_deleted_notice.html', {'rig_name': rig_name})
        response['HX-Redirect'] = '/dashboard/rigs/'
        return response

    return redirect('dashboard:rig-list')


@login_required
@require_POST
def rig_rename(request, uuid):
    """Rename a rig. Accepts both form POST and HTMX POST."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404

    new_name = request.POST.get('name', '').strip()
    if new_name:
        old_name = rig.name
        rig.name = new_name[:128]
        rig.save(update_fields=['name'])
        log_audit_event(request, 'rig.renamed', 'Rig', rig.uuid, {
            'old_name': old_name,
            'new_name': rig.name,
        })
        # Invalidate cached rig data (name changed; cached rig still has old name)
        invalidate_rig_cache(uuid)

    if request.headers.get('HX-Request'):
        return render(request, 'dashboard/_rig_name.html', {'rig': rig})

    return redirect('dashboard:rig-detail', uuid=uuid)


@login_required
@rate_limit(max_requests=30, window_s=60)
def htmx_report_data(request, uuid):
    """HTMX endpoint: renders report table partial for a rig.

    Fetches aggregated report data and passes it to _report_table.html.
    Uses cached Rig lookup to avoid DB query per request.
    """
    rig = _get_rig_light_cached(uuid, request.user)
    if rig is None:
        raise Http404

    range_hours = int(request.GET.get('range_hours', 24))
    if range_hours not in (24, 168, 720):
        range_hours = 24

    # Cache report data per rig+range (55s TTL — under heartbeat interval)
    cache_key = f'report_{uuid}_{range_hours}'
    context = cache.get(cache_key)
    if context is None:
        context = _build_report_context(uuid, str(uuid), range_hours)
        cache.set(cache_key, context, 55)

    # Add user-specific cost estimate (not cached — depends on user settings)
    try:
        rate = float(request.user.electricity_rate_kwh)
        context['power_cost_estimate'] = round(context['power_total_kwh'] * rate, 2)
    except Exception:
        context['power_cost_estimate'] = None

    return render(request, 'dashboard/_report_table.html', context)


def _build_report_context(uuid, uuid_str, range_hours):
    """Build the report context dict (separated for caching).

    Performance strategy:
    - For tables that ARE compacted (GPUMetric, StorageMetric, NetworkMetric),
      use SQL aggregation at the chart's bucket size. For 7d/30d ranges,
      this means scanning ~700 rows instead of ~10000 raw rows.
    - For MetricSnapshot (NOT compacted), use a single query with all aggregations.
    - The power cost (kWh) calculation is derived from the existing
      snap_agg total_system_power_w_avg — no separate query needed.
    - Caching at the view level (55s TTL) handles the common case of repeated loads.

    Query count for range_hours=24 (1-min buckets): 4 queries
        1. GPUMetric aggregation
        2. MetricSnapshot aggregation (CPU/Memory/Power/Errors)
        3. StorageMetric aggregation
        4. NetworkMetric aggregation

    Query count for range_hours=168/720 (15-min/1-hour buckets): same 4 queries
        but each scans ~30x fewer rows due to pre-bucketed data.
    """
    now = timezone.now()
    start = now - timedelta(hours=range_hours)
    base_filter = dict(rig_uuid=uuid_str, timestamp__gte=start, timestamp__lte=now)

    from django.db.models import Avg, Max, Sum

    # Query 1: GPU metrics aggregation
    # Scans all raw rows in range; for 7d/30d the data is mostly tier 2/3
    # (pre-bucketed) so the actual row count is ~700 instead of ~10000.
    gpu_devices = list(
        GPUMetric.objects.filter(**base_filter)
        .values('gpu_index', 'model')
        .annotate(
            gpu_temp_c_avg=Avg('gpu_temp_c'),
            gpu_temp_c_max=Max('gpu_temp_c'),
            gpu_util_pct_avg=Avg('gpu_util_pct'),
            gpu_util_pct_max=Max('gpu_util_pct'),
            mem_controller_util_pct_avg=Avg('mem_controller_util_pct'),
            mem_controller_util_pct_max=Max('mem_controller_util_pct'),
            power_draw_w_avg=Avg('power_draw_w'),
            power_draw_w_max=Max('power_draw_w'),
            mem_used_mb_avg=Avg('mem_used_mb'),
            mem_used_mb_max=Max('mem_used_mb'),
            fan_speed_pct_avg=Avg('fan_speed_pct'),
            fan_speed_pct_max=Max('fan_speed_pct'),
            gpu_core_clock_mhz_avg=Avg('gpu_core_clock_mhz'),
            gpu_core_clock_mhz_max=Max('gpu_core_clock_mhz'),
            gpu_mem_clock_mhz_avg=Avg('gpu_mem_clock_mhz'),
            gpu_mem_clock_mhz_max=Max('gpu_mem_clock_mhz'),
        ).order_by('gpu_index')
    )

    # Query 2: CPU / Memory / Power / Errors aggregation
    # MetricSnapshot is NOT compacted, so this always scans raw rows.
    # For 24h range: 1440 rows, for 7d: 10080 rows, for 30d: 43200 rows.
    # AVG/MAX aggregates are O(N) at DB level, so query time scales linearly.
    snap_agg = MetricSnapshot.objects.filter(**base_filter).aggregate(
        cpu_utilization_pct_avg=Avg('cpu_utilization_pct'),
        cpu_utilization_pct_max=Max('cpu_utilization_pct'),
        cpu_temp_c_avg=Avg('cpu_temp_c'),
        cpu_temp_c_max=Max('cpu_temp_c'),
        cpu_power_w_avg=Avg('cpu_power_w'),
        cpu_power_w_max=Max('cpu_power_w'),
        cpu_freq_current_mhz_avg=Avg('cpu_freq_current_mhz'),
        cpu_freq_current_mhz_max=Max('cpu_freq_current_mhz'),
        mem_used_bytes_avg=Avg('mem_used_bytes'),
        mem_used_bytes_max=Max('mem_used_bytes'),
        swap_used_bytes_avg=Avg('swap_used_bytes'),
        swap_used_bytes_max=Max('swap_used_bytes'),
        total_system_power_w_avg=Avg('total_system_power_w'),
        total_system_power_w_max=Max('total_system_power_w'),
        error_count_sum=Sum('error_count'),
    )

    # Query 3: Storage metrics per device
    disk_devices = list(
        StorageMetric.objects.filter(**base_filter)
        .values('device', 'mountpoint')
        .annotate(
            disk_usage_pct_max=Max('usage_pct'),
            disk_read_bytes_sum=Sum('read_bytes_delta'),
            disk_write_bytes_sum=Sum('write_bytes_delta'),
            disk_read_iops_max=Max('read_iops_delta'),
            disk_write_iops_max=Max('write_iops_delta'),
            disk_utilization_pct_max=Max('utilization_pct'),
        ).order_by('device')
    )

    # Query 4: Network metrics per interface
    net_interfaces = list(
        NetworkMetric.objects.filter(**base_filter)
        .values('interface')
        .annotate(
            net_rx_bytes_sum=Sum('rx_bytes_delta'),
            net_tx_bytes_sum=Sum('tx_bytes_delta'),
            net_rx_errors_sum=Sum('rx_errors'),
            net_tx_errors_sum=Sum('tx_errors'),
        ).order_by('interface')
    )

    # Calculate power_total_kwh from the existing aggregation.
    # Previously: separate query that did TruncMinute/TruncHour grouping.
    # Now: derive from total_system_power_w_avg * range_hours.
    # This is less precise (assumes constant power over the range) but
    # avoids a 5th DB query.
    avg_power_w = snap_agg.get('total_system_power_w_avg') or 0
    power_total_kwh = round((avg_power_w * range_hours) / 1000, 3)

    return {
        'range_hours': range_hours,
        'gpu_devices': gpu_devices,
        'disk_devices': disk_devices,
        'net_interfaces': net_interfaces,
        'power_total_kwh': power_total_kwh,
        'power_cost_estimate': None,
        **snap_agg,
    }
