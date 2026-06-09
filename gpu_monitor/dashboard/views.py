from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.views.decorators.http import require_POST

from rigs.models import Rig, RigTag
from metrics_app.models import MetricSnapshot, LatestSnapshot, GPUMetric, GPUProcessMetric, StorageMetric, NetworkMetric, DockerContainerMetric, ErrorEvent
from audit.middleware import log_audit_event


def _fetch_rig_metrics(uuid):
    """Fetch the latest rig metrics for Live Metrics display.

    Uses SQL-level latest-per-device queries instead of fetching all rows.
    """
    try:
        snapshot = LatestSnapshot.objects.get(rig_uuid=str(uuid))
    except LatestSnapshot.DoesNotExist:
        snapshot = None

    # GPU: latest metric per unique GPU using DISTINCT ON
    gpu_metrics = list(
        GPUMetric.objects.filter(rig_uuid=str(uuid))
        .order_by('gpu_uuid', '-timestamp')
        .distinct('gpu_uuid')
        .order_by('gpu_uuid', 'gpu_index')
    )

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

    # Docker containers (last 20)
    docker_metrics = list(
        DockerContainerMetric.objects.filter(rig_uuid=str(uuid))
        .order_by('-timestamp')[:20]
    )

    # Recent errors
    recent_errors = list(
        ErrorEvent.objects.filter(rig_uuid=str(uuid))
        .order_by('-last_seen')[:5]
    )

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
        gpus = list(
            GPUMetric.objects.filter(rig_uuid=str(rig.uuid))
            .order_by('gpu_uuid', '-timestamp')
            .distinct('gpu_uuid')
            .order_by('gpu_uuid', 'gpu_index')
        )

        rig_data.append({'rig': rig, 'snapshot': snap, 'gpus': gpus})

    if request.headers.get('HX-Request'):
        return render(request, 'dashboard/_rig_table.html', {'rig_data': rig_data})

    all_tags = RigTag.objects.filter(user=user).order_by('name')

    return render(request, 'dashboard/rig_list.html', {
        'rig_data': rig_data,
        'status_filter': status_filter,
        'search': search,
        'all_tags': all_tags,
        'tag_filter': tag_filter,
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
def rig_detail(request, uuid):
    """Rig detail page."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404

    context = _fetch_rig_metrics(uuid)
    context['rig'] = rig
    context['is_data_stale'] = rig.status in [Rig.Status.OFFLINE, Rig.Status.STALE]

    return render(request, 'dashboard/rig_detail.html', context)


@login_required
def htmx_metrics(request, uuid):
    """HTMX polling endpoint for live metrics."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404

    context = _fetch_rig_metrics(uuid)
    context['rig'] = rig
    context['is_data_stale'] = rig.status in [Rig.Status.OFFLINE, Rig.Status.STALE]

    return render(request, 'dashboard/_metrics_cards.html', context)


@login_required
def htmx_rig_status(request, uuid):
    """HTMX polling endpoint — returns just the status badge + last_seen."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404
    return render(request, 'dashboard/_rig_status_badge.html', {'rig': rig})


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
