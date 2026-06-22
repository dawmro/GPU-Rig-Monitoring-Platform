from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from .models import AuditLog


@login_required
def audit_log_view(request):
    """Activity feed page showing audit log entries."""
    # Filter by user's own actions (or all for staff)
    if request.user.is_staff:
        logs = AuditLog.objects.all()
    else:
        logs = AuditLog.objects.filter(user=request.user)

    # Filters
    action_filter = request.GET.get('action', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if action_filter:
        logs = logs.filter(action=action_filter)
    if date_from:
        logs = logs.filter(timestamp__gte=date_from)
    if date_to:
        logs = logs.filter(timestamp__lte=date_to)

    # Pagination
    paginator = Paginator(logs, 50)
    page = request.GET.get('page', 1)
    logs_page = paginator.get_page(page)

    # Get distinct actions for filter dropdown
    actions = AuditLog.objects.values_list('action', flat=True).distinct().order_by('action')

    return render(request, 'audit/audit_log.html', {
        'logs': logs_page,
        'actions': actions,
        'action_filter': action_filter,
        'date_from': date_from,
        'date_to': date_to,
    })
