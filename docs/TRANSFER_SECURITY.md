# API Key Transfer — Security Analysis and Plan

## The Problem: Information Leakage

### Current Design Flaw
The transfer UI requires the transferring user to select a target user from a dropdown. This means:
- User A sees ALL other users' emails in the dropdown
- User A knows who else uses the platform
- This is private information that regular users should not have access to

### Real-World Attack Scenario
1. User A wants to sabotage User B
2. User A creates a fake rig, gets an API key
3. User A transfers the key to User B (without User B's consent)
4. Now User B has an unknown API key in their account
5. If User A still has the key value, they can send fake data to User B's account
6. User B's dashboard is polluted with fake rig data

### Even Worse: Mass Transfer Attack
1. User A creates 100 fake rigs with one API key
2. User A transfers all rigs to User B
3. User B's dashboard is flooded with 100 fake rigs
4. User B has to manually delete them all

## Recommended Solution: Admin-Only Transfer

### Principle
Only staff/admin users should be able to transfer API keys between users. This is an administrative operation that requires elevated privileges.

### Implementation Plan

#### Approach 1: Admin Panel Transfer (RECOMMENDED)

**How it works:**
1. Admin goes to Django admin panel
2. Selects API keys to transfer
3. Chooses target user from autocomplete
4. Admin confirms transfer
5. System transfers keys + updates rig ownership

**Advantages:**
- No information leakage to regular users
- Admin has full audit trail
- Can be done via existing Django admin infrastructure
- No new UI needed

**Implementation:**

```python
# accounts/admin.py
from django.contrib import admin
from .models import ApiKey, User
from django.contrib import messages

@admin.action(description='Transfer selected keys to another user')
def transfer_keys(modeladmin, request, queryset):
    # This would need a form intermediate step
    # See custom admin view below
    pass
```

Better approach — custom admin view:

```python
# accounts/admin.py
from django.contrib import admin
from django.shortcuts import render, redirect
from django.contrib import messages
from .models import ApiKey, Rig
from django.contrib.auth import get_user_model

User = get_user_model()

@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'is_active', 'rig_count', 'transfer_count', 'created_at']
    list_filter = ['is_active', 'user']
    search_fields = ['name', 'user__email']
    
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/transfer/',
                self.admin_site.admin_view(self.transfer_view),
                name='accounts_apikey_transfer',
            ),
        ]
        return custom_urls + urls
    
    def transfer_view(self, request, object_id):
        key = self.get_object(request, object_id)
        
        if request.method == 'POST':
            target_user_id = request.POST.get('target_user')
            if target_user_id:
                target_user = User.objects.get(id=target_user_id)
                if target_user == key.user:
                    messages.error(request, 'Cannot transfer to the same user.')
                else:
                    # Perform transfer
                    old_user = key.user
                    key.user = target_user
                    key.save()
                    
                    # Update rig ownership
                    rig_count = key.enrolled_rigs.count()
                    Rig.objects.filter(enrolled_by_api_key=key).update(owner=target_user)
                    
                    messages.success(
                        request,
                        f'Key "{key.name}" transferred from {old_user.email} to {target_user.email}. '
                        f'{rig_count} rig(s) affected.'
                    )
                    return redirect('admin:accounts_apikey_changelist')
        
        users = User.objects.exclude(id=key.user.id).order_by('email')
        return render(request, 'admin/accounts/apikey/transfer.html', {
            'key': key,
            'users': users,
        })
```

**Template (`templates/admin/accounts/apikey/transfer.html`):**

```html
{% extends "admin/base_site.html" %}
{% block content %}
<h1>Transfer API Key: {{ key.name }}</h1>

<div class="module">
    <h2>Key Details</h2>
    <table>
        <tr><th>Name:</th><td>{{ key.name }}</td></tr>
        <tr><th>Current Owner:</th><td>{{ key.user.email }}</td></tr>
        <tr><th>Status:</th><td>{% if key.is_active %}Active{% else %}Revoked{% endif %}</td></tr>
        <tr><th>Enrolled Rigs:</th><td>{{ key.enrolled_rigs.count }}</td></tr>
        <tr><th>Transfer Count:</th><td>{{ key.transfer_count }}</td></tr>
    </table>
</div>

{% if key.enrolled_rigs.count > 0 %}
<div class="module">
    <h2>Rigs that will be transferred</h2>
    <ul>
        {% for rig in key.enrolled_rigs.all %}
        <li>{{ rig.name }} ({{ rig.uuid }}) — {{ rig.status }}</li>
        {% endfor %}
    </ul>
</div>
{% endif %}

<div class="module">
    <h2>Select Target User</h2>
    <form method="post">
        {% csrf_token %}
        <select name="target_user" required>
            <option value="">Select user...</option>
            {% for user in users %}
            <option value="{{ user.id }}">{{ user.email }}</option>
            {% endfor %}
        </select>
        <input type="submit" value="Transfer" class="default">
    </form>
</div>

<a href="{% url 'admin:accounts_apikey_changelist' %}">Cancel</a>
{% endblock %}
```

#### Approach 2: Dedicated Admin Page in Dashboard

If you want the transfer UI in the main dashboard (not Django admin):

**How it works:**
1. Only staff users see "Transfer Rigs" nav link
2. Admin selects source user → sees their keys → selects target user
3. Admin confirms transfer

**Advantages:**
- More user-friendly than Django admin
- Can show previews of what will be transferred
- Better UX for bulk operations

**Disadvantages:**
- Requires building new UI
- More development time

#### Approach 3: Request-Based Transfer (Most Secure)

**How it works:**
1. User A requests to transfer a rig to User B
2. Admin receives notification
3. Admin approves or rejects
4. If approved, transfer happens automatically

**Advantages:**
- Most secure — requires explicit approval from both sides
- Full audit trail
- Prevents unauthorized transfers

**Disadvantages:**
- Complex workflow
- Requires notification system
- Slower process

## Final Decision: Dedicated Admin Page in Dashboard (IMPLEMENTED)

The transfer functionality is implemented as a dedicated admin page at `/accounts/admin/transfer-keys/`, accessible only to staff users.

### Why This Approach
- More user-friendly than Django admin panel
- Clear 3-step flow: select source → select keys → select target
- Shows preview of what will be transferred (keys + rigs)
- Full audit trail via `log_audit_event`
- No information leakage (only staff sees other users' emails)

### What Was Removed
- User-facing transfer UI was removed from `api_keys.html`
- `transfer_api_keys` view was removed
- `transfer-api-keys` URL was removed
- Regular users never see transfer functionality

### Security Guarantees
- Only staff users can access the transfer page
- Regular users never see other users' emails
- All transfers are logged
- Admin reviews what will be transferred before confirming
- Cannot transfer to self
- CSRF protection
