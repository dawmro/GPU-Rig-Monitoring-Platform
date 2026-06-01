from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.utils import timezone
from datetime import timedelta
from django.views.decorators.http import require_POST

from rigs.models import Rig
from metrics_app.models import LatestSnapshot


@login_required
def rig_list(request):
    """Fleet overview page."""
    user = request.user
    rigs = Rig.objects.filter(owner=user).prefetch_related('tags').order_by('-last_seen')

    status_filter = request.GET.get('status', '')
    if status_filter:
        rigs = rigs.filter(status=status_filter)

    search = request.GET.get('search', '')
    if search:
        rigs = rigs.filter(name__icontains=search)

    tag_filter = request.GET.get('tag', '')
    if tag_filter:
        rigs = rigs.filter(tags__name=tag_filter)

    # Attach latest snapshots
    rig_data = []
    for rig in rigs:
        try:
            snap = LatestSnapshot.objects.get(rig_uuid=str(rig.uuid))
        except LatestSnapshot.DoesNotExist:
            snap = None
        rig_data.append({'rig': rig, 'snapshot': snap})

    if request.headers.get('HX-Request'):
        return render(request, 'dashboard/_rig_table.html', {'rig_data': rig_data})

    return render(request, 'dashboard/rig_list.html', {
        'rig_data': rig_data,
        'status_filter': status_filter,
        'search': search,
    })


@login_required
def rig_detail(request, uuid):
    """Rig detail page."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404

    try:
        snapshot = LatestSnapshot.objects.get(rig_uuid=str(uuid))
    except LatestSnapshot.DoesNotExist:
        snapshot = None

    return render(request, 'dashboard/rig_detail.html', {
        'rig': rig,
        'snapshot': snapshot,
    })


@login_required
def htmx_metrics(request, uuid):
    """HTMX polling endpoint for live metrics."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404

    try:
        snapshot = LatestSnapshot.objects.get(rig_uuid=str(uuid))
    except LatestSnapshot.DoesNotExist:
        snapshot = None

    return render(request, 'dashboard/_metrics_cards.html', {
        'rig': rig,
        'snapshot': snapshot,
    })


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
