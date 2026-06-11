from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseTooManyRequests
from django.views.decorators.http import require_POST
from django.db.models import Count
from django.core.cache import cache
from functools import wraps
import time

from rigs.models import Rig, RigTag
from metrics_app.models import MetricSnapshot, LatestSnapshot, GPUMetric, GPUProcessMetric, StorageMetric, NetworkMetric, DockerContainerMetric, LatestDockerContainer
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
                return HttpResponseTooManyRequests(
                    'Rate limit exceeded. Please slow down.',
                    content_type='text/plain'
                )

            timestamps.append(now)
            cache.set(key, timestamps, timeout=window_s)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _format_uptime(uptime_s):
    """Format uptime seconds as human-readable string."""
    if uptime_s is None:
        return '—'
    if uptime_s >= 86400:
        return f'{uptime_s // 86400}d'
    if uptime_s >= 3600:
        return f'{uptime_s // 3600}h'
    return f'{uptime_s}s'


def _format_mem(usage_bytes, limit_bytes):
    """Format memory as 'usage / limit' string (e.g. '50 MB / 400 MB')."""
    if not usage_bytes:
        return '—'
    from django.template.defaultfilters import filesizeformat
    usage_str = filesizeformat(usage_bytes)
    if limit_bytes:
        return f'{usage_str} / {filesizeformat(limit_bytes)}'
    return usage_str


def _fetch_rig_metrics(uuid, rig=None):
    """Fetch the latest rig metrics for Live Metrics display.

    Uses SQL-level latest-per-device queries instead of fetching all rows.
    """
    try:
        snapshot = LatestSnapshot.objects.get(rig_uuid=str(uuid))
    except LatestSnapshot.DoesNotExist:
        snapshot = None

    # GPU: latest metric per unique GPU using DISTINCT ON
    # Sort by gpu_index (0, 1, 2...) for consistent display order
    gpu_metrics = list(
        GPUMetric.objects.filter(rig_uuid=str(uuid))
        .order_by('gpu_index', '-timestamp')
        .distinct('gpu_index')
    )
    gpu_metrics.sort(key=lambda g: g.gpu_index)

    # Storage: latest metric per unique device using DISTINCT ON
    storage_metrics = list(
        StorageMetric.objects.filter(rig_uuid=str(uuid))
        .order_by('device', '-timestamp')
        .distinct('device')
    )

    # Network: latest metric per unique interface using DISTINCT ON
    network_metrics = list(
        NetworkMetric.objects.filter(rig_uuid=str(uuid))
        .order_by('interface', '-timestamp')
        .distinct('interface')
    )

    # Docker containers: combine LatestDockerContainer (display fields) with
    # latest DockerContainerMetric (cpu/memory) for each container
    latest_containers = LatestDockerContainer.objects.filter(
        rig_uuid=str(uuid)
    )

    docker_metrics = []
    for lc in latest_containers:
        # Get the latest metric row for this container's CPU/memory
        latest_metric = DockerContainerMetric.objects.filter(
            rig_uuid=str(uuid),
            name=lc.name
        ).order_by('-timestamp').first()

        uptime_str = _format_uptime(lc.uptime_s)
        mem_str = _format_mem(
            latest_metric.mem_usage_bytes if latest_metric else None,
            lc.mem_limit_bytes
        )

        docker_metrics.append({
            'container_id': lc.container_id,
            'name': lc.name,
            'image': lc.image,
            'status': lc.status,
            'restart_count': lc.restart_count,
            'uptime_s': lc.uptime_s,
            'uptime_str': uptime_str,
            'cpu_pct': latest_metric.cpu_pct if latest_metric else None,
            'mem_usage_bytes': latest_metric.mem_usage_bytes if latest_metric else None,
            'mem_limit_bytes': lc.mem_limit_bytes,
            'mem_str': mem_str,
        })

    # Sort by uptime descending (longest running first)
    docker_metrics.sort(key=lambda c: c['uptime_s'] or 0, reverse=True)

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

    return {
        'snapshot': snapshot,
        'gpu_metrics': gpu_metrics,
        'gpu_processes': gpu_processes,
        'storage_metrics': storage_metrics,
        'network_metrics': network_metrics,
        'docker_metrics': docker_metrics,
        'recent_errors': recent_errors,
        'metric_snapshot': latest_metric_snapshot,
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
    rig_data = []
    for rig in rigs:
        try:
            snap = LatestSnapshot.objects.get(rig_uuid=str(rig.uuid))
        except LatestSnapshot.DoesNotExist:
            snap = None

        # Fetch latest GPU metric per unique GPU using DISTINCT ON
        # Sort by gpu_index (0, 1, 2...) for consistent display order
        gpus = list(
            GPUMetric.objects.filter(rig_uuid=str(rig.uuid))
            .order_by('gpu_index', '-timestamp')
            .distinct('gpu_index')
            .order_by('gpu_index')
        )

        rig_data.append({'rig': rig, 'snapshot': snap, 'gpus': gpus})

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
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404
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
        StorageMetric, NetworkMetric, DockerContainerMetric, LatestDockerContainer, RigStatusEvent
    MetricSnapshot.objects.filter(rig_uuid=uuid).delete()
    LatestSnapshot.objects.filter(rig_uuid=uuid).delete()
    GPUMetric.objects.filter(rig_uuid=uuid).delete()
    GPUProcessMetric.objects.filter(rig_uuid=uuid).delete()
    StorageMetric.objects.filter(rig_uuid=uuid).delete()
    NetworkMetric.objects.filter(rig_uuid=uuid).delete()
    DockerContainerMetric.objects.filter(rig_uuid=uuid).delete()
    LatestDockerContainer.objects.filter(rig_uuid=uuid).delete()
    RigStatusEvent.objects.filter(rig_uuid=uuid).delete()

    rig.delete()
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
