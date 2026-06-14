from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_POST
from django.db.models import Count
from django.core.cache import cache
from functools import wraps
import time

from rigs.models import Rig, RigTag
from metrics_app.models import MetricSnapshot, LatestSnapshot, GPUMetric, GPUProcessMetric, StorageMetric, NetworkMetric, LatestDockerContainer
from audit.middleware import log_audit_event


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


def _fetch_rig_metrics(uuid, rig=None):
    """Fetch the latest rig metrics for Live Metrics display.

    Uses SQL-level latest-per-device queries instead of fetching all rows.
    """
    # LatestSnapshot changes only on heartbeat (~60s), but is polled every 30s.
    # Cache with 50s TTL to reduce DB reads between heartbeats.
    snapshot = None
    if rig:
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
                'model': _json_get(snapshot.gpu_models_json, i, ''),
                'gpu_temp_c': _json_get(snapshot.gpu_temps_json, i),
                'gpu_util_pct': _json_get(snapshot.gpu_utils_json, i),
                'fan_speed_pct': _json_get(snapshot.gpu_fans_json, i),
                'gpu_core_clock_mhz': _json_get(snapshot.gpu_core_clocks_json, i),
                'gpu_mem_clock_mhz': _json_get(snapshot.gpu_mem_clocks_json, i),
                'mem_used_mb': _json_get(snapshot.gpu_mem_used_json, i),
                'mem_total_mb': _json_get(snapshot.gpu_mem_total_json, i),
                'mem_util_pct': _json_get(snapshot.gpu_mem_util_pcts_json, i),
                'mem_free_mb': _json_get(snapshot.gpu_mem_free_json, i),
                'power_draw_w': _json_get(snapshot.gpu_power_draws_json, i),
                'power_limit_w': _json_get(snapshot.gpu_power_limits_json, i),
                'pcie_current_gen': _json_get(snapshot.gpu_pcie_gen_json, i),
                'pcie_max_gen': _json_get(snapshot.gpu_pcie_max_gen_json, i),
                'pcie_current_width': _json_get(snapshot.gpu_pcie_width_json, i),
                'pcie_max_width': _json_get(snapshot.gpu_pcie_max_width_json, i),
                'gpu_uuid': '',  # Not stored in snapshot
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
    latest_containers = LatestDockerContainer.objects.filter(
        rig_uuid=str(uuid)
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
        })

    # Sort: running/restarting first, then by name
    status_order = {'running': 0, 'restarting': 1, 'exited': 2}
    docker_metrics.sort(key=lambda c: (status_order.get(c['status'], 9), c['name']))

    # Recent errors from Rig.latest_errors_json (latest payload only, like motherboard_json)
    recent_errors = rig.latest_errors_json if rig else []

    # Latest MetricSnapshot for motherboard/software JSON
    latest_metric_snapshot = MetricSnapshot.objects.filter(
        rig_uuid=str(uuid)
    ).order_by('-timestamp').first()

    # GPU processes (latest per GPU per pid)
    gpu_processes = list(
        GPUProcessMetric.objects.filter(rig_uuid=str(uuid))
        .order_by('-timestamp')[:50]
    )

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

    return {
        'snapshot': snapshot,
        'gpu_metrics': gpu_metrics,
        'gpu_processes': gpu_processes,
        'storage_metrics': storage_metrics,
        'network_metrics': network_metrics,
        'docker_metrics': docker_metrics,
        'recent_errors': recent_errors,
        'metric_snapshot': latest_metric_snapshot,
        'primary_ip': primary_ip,
    }


@login_required
@rate_limit(max_requests=60, window_s=60)
def rig_list(request):
    """Fleet overview page.

    Refreshes every 30s via HTMX (see rig_list.html).
    All table columns are rendered from ``rig_data`` passed to
    ``_rig_table.html``.  To add a new column:

    1. Add the column header to ``_rig_table.html`` <thead>.
    2. Fetch the data here in the ``rig_data`` loop (or annotate the queryset).
    3. Extend the ``rig_data.append({...})`` dict with the new key.
    4. Add the <td> cell in ``_rig_table.html`` <tbody> using the new key.
    """
    user = request.user
    if user.is_staff:
        rigs = Rig.objects.all().prefetch_related('tags', 'owner').order_by('name')
    else:
        rigs = Rig.objects.filter(owner=user).prefetch_related('tags').order_by('name')

    status_filter = request.GET.get('status', '')
    if status_filter:
        rigs = rigs.filter(status=status_filter)

    search = request.GET.get('search', '')
    if search:
        rigs = rigs.filter(name__icontains=search)

    tag_filter = request.GET.get('tag', '')
    if tag_filter:
        rigs = rigs.filter(tags__name=tag_filter)

    # Sort rigs naturally by name (e.g., rig2 before rig11)
    # Python-side sorting after all queryset filtering is complete
    import re
    def _natural_sort_key(value):
        """Split string into text/number chunks for human-friendly sorting."""
        return [
            int(chunk) if chunk.isdigit() else chunk.lower()
            for chunk in re.split(r'(\d+)', value or '')
        ]
    rigs = sorted(rigs, key=lambda r: _natural_sort_key(r.name))

    # Build rig_data dicts consumed by _rig_table.html.
    # Each key maps directly to a template variable in the table cells:
    #   rig_data[]['rig']      -> Rig model (name, status, last_seen, tags, uuid)
    #   rig_data[]['snapshot'] -> LatestSnapshot (cpu_utilization_pct, cpu_temp_c, mem_*)
    #   rig_data[]['gpus']     -> list of latest GPUMetric per unique GPU (by gpu_uuid)

    # Batch-fetch all LatestSnapshot rows in ONE query (avoids N+1)
    rig_uuids = [str(r.uuid) for r in rigs]
    latest_snapshots = {
        str(s.rig_uuid): s  # Use str key to match rig_uuid_str lookups
        for s in LatestSnapshot.objects.filter(rig_uuid__in=rig_uuids)
    }

    # GPU data is now stored directly in LatestSnapshot as JSON arrays.
    # No need to query the GPUMetric timeseries table for fleet overview.
    # Each snapshot has: gpu_count, gpu_models_json, gpu_temps_json,
    # gpu_utils_json, gpu_fans_json — one entry per GPU, ordered by gpu_index.

    # Build rig_data using snapshot data (no GPUMetric queries needed)
    rig_data = []
    for rig in rigs:
        rig_uuid_str = str(rig.uuid)
        rig_data.append({
            'rig': rig,
            'snapshot': latest_snapshots.get(rig_uuid_str),
        })

    if request.headers.get('HX-Request'):
        return render(request, 'dashboard/_rig_table.html', {'rig_data': rig_data})

    all_tags = RigTag.objects.filter(user=user).order_by('name') if not user.is_staff else RigTag.objects.all().order_by('name')

    # Count rigs by status for the header display
    rigs_for_counts = Rig.objects.all() if user.is_staff else Rig.objects.filter(owner=user)
    status_counts = dict(rigs_for_counts.values_list('status').annotate(count=Count('status')).values_list('status', 'count'))
    online_count = status_counts.get('online', 0)
    stale_count = status_counts.get('stale', 0)
    offline_count = status_counts.get('offline', 0)
    total_count = online_count + stale_count + offline_count

    return render(request, 'dashboard/rig_list.html', {
        'rig_data': rig_data,
        'status_filter': status_filter,
        'search': search,
        'all_tags': all_tags,
        'tag_filter': tag_filter,
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
    """HTMX polling endpoint for live metrics."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404

    context = _fetch_rig_metrics(uuid, rig)
    context['rig'] = rig
    context['is_data_stale'] = rig.status in [Rig.Status.OFFLINE, Rig.Status.STALE]

    return render(request, 'dashboard/_metrics_cards.html', context)


@login_required
@rate_limit(max_requests=120, window_s=60)
def htmx_rig_status(request, uuid):
    """HTMX polling endpoint — returns just the status badge + last_seen."""
    # Field-selective query: only fetch status, last_seen, owner_id
    # Reduces data transfer for this high-frequency poll (every 15s)
    rig_data = Rig.objects.filter(
        uuid=uuid
    ).values('status', 'last_seen', 'owner_id').first()

    if not rig_data:
        raise Http404
    if rig_data['owner_id'] != request.user.id and not request.user.is_staff:
        raise Http404

    # Use SimpleNamespace to provide attribute access for template
    from types import SimpleNamespace
    rig = SimpleNamespace(**rig_data)

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
    # Invalidate cached snapshot for this rig
    cache.delete(f'lsnap_{uuid}')
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
        rig.name = new_name[:128]
        rig.save(update_fields=['name'])

    if request.headers.get('HX-Request'):
        return render(request, 'dashboard/_rig_name.html', {'rig': rig})

    return redirect('dashboard:rig-detail', uuid=uuid)
