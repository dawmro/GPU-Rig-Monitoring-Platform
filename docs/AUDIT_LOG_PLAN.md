# Audit Log & Activity Feed — Detailed Implementation Plan

## Current State Analysis

### What exists:
- `AuditLog` model with fields: id, timestamp, user FK, action, target_type, target_id, ip_address, metadata_json
- `AuditMiddleware` that captures events from `request._audit_event`
- `log_audit_event()` helper function
- Currently logs 13 action types across accounts, dashboard, and metrics apps

### Currently logged actions:
| Action | App | Target |
|---|---|---|
| `user.registered` | accounts | User |
| `user.password_changed` | accounts | User |
| `apikey.created` | accounts | ApiKey |
| `apikey.revoked` | accounts | ApiKey |
| `apikey.reactivated` | accounts | ApiKey |
| `apikey.deleted` | accounts | ApiKey |
| `apikey.transferred` | accounts | ApiKey |
| `tag.created` | accounts | RigTag |
| `tag.updated` | accounts | RigTag |
| `tag.deleted` | accounts | RigTag |
| `tag.added` | dashboard | Rig |
| `tag.removed` | dashboard | Rig |
| `rig.enrolled` | metrics_app | Rig |
| `rig.deleted` | dashboard | Rig |

### What's NOT logged (gaps):
1. **Login/Logout** — user.session.login, user.session.logout
2. **Rig rename** — rig.renamed (old_name → new_name)
3. **Rig detail view** — rig.viewed (for access tracking)
4. **Fleet overview view** — fleet.viewed
5. **Profile view** — user.profile.viewed
6. **API key page view** — apikey.list.viewed
7. **Tag list view** — tag.list.viewed
8. **Transfer keys page view** — transfer_keys.viewed
9. **Failed login attempts** — user.login.failed
10. **Permission denied** — user.access_denied
11. **Data export** — data.exported (future feature)
12. **Settings changed** — user.settings.changed

## Detailed Implementation Plan

### Phase 1: Extend AuditLog Model (no migration needed initially)

The current model is already well-designed. No changes needed to the model itself.

### Phase 2: Add Missing Audit Events

#### 2.1 Login/Logout Tracking

**File:** `accounts/views.py`

```python
# In login_view, after successful login:
log_audit_event(request, 'user.session.login', 'User', user.id, {
    'ip': ip_address,
    'user_agent': request.META.get('HTTP_USER_AGENT', '')[:200],
})

# In logout_view, before logout:
log_audit_event(request, 'user.session.logout', 'User', request.user.id, {})
```

**Edge cases:**
- Failed login attempts — log with user=None, action='user.login.failed', metadata with attempted email
- Session expiry — can't log (no request context), acceptable gap
- Multiple concurrent sessions — each login creates separate event

#### 2.2 Rig Rename Tracking

**File:** `dashboard/views.py`

```python
# In rig_rename view:
log_audit_event(request, 'rig.renamed', 'Rig', rig.uuid, {
    'old_name': old_name,
    'new_name': name,
})
```

**Edge cases:**
- Same name (no actual change) — still log, metadata shows old=new
- Empty name — log with empty string
- Very long names — truncate in metadata

#### 2.3 Page View Tracking (optional, for access audit)

**File:** Middleware-based approach for tracking page views

```python
# In AuditMiddleware.__call__, after response:
if request.method == 'GET' and response.status_code == 200:
    # Only track specific pages, not every HTMX poll
    if not request.headers.get('HX-Request'):
        path = request.path_info
        if path.startswith('/dashboard/rigs/') and len(path) > 15:
            # Rig detail page
            log_audit_event(request, 'rig.viewed', 'Rig', uuid_from_path, {})
        elif path == '/dashboard/rigs/':
            log_audit_event(request, 'fleet.viewed', 'Page', 'fleet', {})
```

**Edge cases:**
- HTMX polling — don't log (would flood audit log)
- Search/filter requests — don't log (too frequent)
- Static files — middleware already skips these
- API endpoints — don't log reads, only writes

#### 2.4 Failed Login Tracking

**File:** `accounts/views.py`

```python
# In login_view, after failed authentication:
log_audit_event(request, 'user.login.failed', 'User', None, {
    'attempted_email': email[:100],  # Truncate for privacy
    'ip': ip_address,
})
```

**Edge cases:**
- Brute force attempts — will create many events, consider rate limiting
- Email truncation — prevent log injection
- Non-existent users — user=None is fine

#### 2.5 Permission Denied Tracking

**File:** New middleware or decorator

```python
# In views that check permissions:
if rig.owner_id != request.user.id and not request.user.is_staff:
    log_audit_event(request, 'user.access_denied', 'Rig', uuid, {
        'attempted_action': 'view',
        'owner_id': rig.owner_id,
    })
    raise Http404
```

**Edge cases:**
- Don't log 404s from non-existent resources (noise)
- Only log when user is authenticated but lacks permission
- Staff bypass — don't log when staff accesses (expected behavior)

### Phase 3: Activity Feed UI

#### 3.1 New URL and View

**URL:** `/accounts/audit-log/`

**View:** `audit_log_view` in new `audit/views.py`

```python
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
    from django.core.paginator import Paginator
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
```

#### 3.2 Template: `audit/audit_log.html`

```html
{% extends "base.html" %}
{% block title %}Activity Feed{% endblock %}
{% block content %}
<div class="max-w-4xl mx-auto">
    <h2 class="text-xl font-bold mb-4">Activity Feed</h2>
    
    <!-- Filters -->
    <form class="flex flex-wrap gap-3 mb-4" method="get">
        <select name="action" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm">
            <option value="">All Actions</option>
            {% for action in actions %}
            <option value="{{ action }}" {% if action == action_filter %}selected{% endif %}>
                {{ action }}
            </option>
            {% endfor %}
        </select>
        <input type="date" name="date_from" value="{{ date_from }}"
               class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm">
        <input type="date" name="date_to" value="{{ date_to }}"
               class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm">
        <button type="submit" class="bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded text-sm">Filter</button>
    </form>
    
    <!-- Log entries -->
    <div class="bg-gray-800 rounded-lg border border-gray-700">
        <table class="w-full text-sm">
            <thead class="bg-gray-750 text-gray-400 border-b border-gray-700">
                <tr>
                    <th class="text-left px-3 py-2">Timestamp</th>
                    <th class="text-left px-3 py-2">Action</th>
                    <th class="text-left px-3 py-2">Target</th>
                    <th class="text-left px-3 py-2">Details</th>
                    <th class="text-left px-3 py-2">IP Address</th>
                </tr>
            </thead>
            <tbody>
                {% for log in logs %}
                <tr class="border-b border-gray-700/50">
                    <td class="px-3 py-2 text-gray-400 whitespace-nowrap">
                        {{ log.timestamp|date:"Y-m-d H:i:s" }}
                    </td>
                    <td class="px-3 py-2">
                        <span class="badge px-1.5 py-0.5 rounded text-xs
                            {% if 'deleted' in log.action %}bg-red-900 text-red-300
                            {% elif 'created' in log.action %}bg-green-900 text-green-300
                            {% elif 'login' in log.action %}bg-blue-900 text-blue-300
                            {% elif 'failed' in log.action or 'denied' in log.action %}bg-red-900 text-red-300
                            {% else %}bg-gray-700 text-gray-300{% endif %}">
                            {{ log.action }}
                        </span>
                    </td>
                    <td class="px-3 py-2 text-gray-400">
                        {% if log.target_type %}
                            {{ log.target_type }}:{{ log.target_id|truncatechars:12 }}
                        {% else %}—{% endif %}
                    </td>
                    <td class="px-3 py-2 text-gray-400 text-xs">
                        {% if log.metadata_json %}
                            {% for key, value in log.metadata_json.items %}
                                <div><span class="text-gray-500">{{ key }}:</span> {{ value }}</div>
                            {% endfor %}
                        {% else %}—{% endif %}
                    </td>
                    <td class="px-3 py-2 text-gray-500 text-xs">
                        {{ log.ip_address|default:"—" }}
                    </td>
                </tr>
                {% empty %}
                <tr>
                    <td colspan="5" class="px-4 py-8 text-center text-gray-500">
                        No audit log entries found.
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    
    <!-- Pagination -->
    {% if logs.has_other_pages %}
    <div class="flex items-center justify-center gap-2 mt-4">
        {% if logs.has_previous %}
            <a href="?page={{ logs.previous_page_number }}&action={{ action_filter }}&date_from={{ date_from }}&date_to={{ date_to }}"
               class="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded text-sm">← Prev</a>
        {% endif %}
        <span class="text-gray-400 text-sm">Page {{ logs.number }} of {{ logs.paginator.num_pages }}</span>
        {% if logs.has_next %}
            <a href="?page={{ logs.next_page_number }}&action={{ action_filter }}&date_from={{ date_from }}&date_to={{ date_to }}"
               class="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded text-sm">Next →</a>
        {% endif %}
    </div>
    {% endif %}
</div>
{% endblock %}
```

#### 3.3 URL Configuration

**File:** `audit/urls.py` (new)

```python
from django.urls import path
from . import views

app_name = 'audit'

urlpatterns = [
    path('accounts/audit-log/', views.audit_log_view, name='audit-log'),
]
```

**File:** `accounts/urls.py` — add include

```python
path('accounts/audit-log/', audit_views.audit_log_view, name='audit-log'),
```

#### 3.4 Navigation Link

Add to base.html nav:

```html
<a href="{% url 'audit:audit-log' %}" class="text-gray-400 hover:text-white">Activity</a>
```

### Phase 4: Data Retention

**Management command:** `cleanup_audit_log.py`

```python
from django.core.management.base import BaseCommand
from audit.models import AuditLog
from datetime import timedelta
from django.utils import timezone

class Command(BaseCommand):
    help = 'Clean up old audit log entries'
    
    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90, help='Retention period in days')
    
    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options['days'])
        deleted, _ = AuditLog.objects.filter(timestamp__lt=cutoff).delete()
        self.stdout.write(f'Deleted {deleted} audit log entries older than {options["days"]} days')
```

**Schedule:** Add to `data_retention.sh`:
```bash
python manage.py cleanup_audit_log --days 90
```

### Edge Cases Summary

| Edge Case | Handling |
|---|---|
| Failed logins | Log with user=None, action='user.login.failed' |
| Brute force | Many events created — consider rate limiting in future |
| Session expiry | Can't log (no request) — acceptable gap |
| HTMX polling | Don't log page views for HTMX requests |
| Long metadata | Truncate strings in metadata to prevent log injection |
| Non-existent resources | Don't log 404s (noise) |
| Staff access | Don't log staff bypasses (expected behavior) |
| Data retention | Cleanup command deletes entries older than 90 days |
| Pagination | 50 entries per page, filterable by action and date |
| Permission | Users see own logs, staff sees all |
| IP behind proxy | Use X-Forwarded-For header (already implemented) |
| Concurrent sessions | Each login creates separate event |
| Same-name rename | Still log, metadata shows old=new |

### Files to Create/Modify

| File | Action | Lines |
|---|---|---|
| `audit/views.py` | Create new | ~60 |
| `audit/urls.py` | Create new | ~10 |
| `audit/templates/audit/audit_log.html` | Create new | ~120 |
| `audit/management/commands/cleanup_audit_log.py` | Create new | ~25 |
| `accounts/views.py` | Modify — add login/logout/failed login events | ~15 |
| `dashboard/views.py` | Modify — add rename event | ~5 |
| `base.html` | Modify — add Activity nav link | ~1 |
| `accounts/urls.py` | Modify — add audit-log URL | ~1 |
| `data_retention.sh` | Modify — add cleanup command | ~1 |

**Total effort: ~1 day**
