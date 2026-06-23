import secrets
from decimal import Decimal
from django.db import models
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.utils import timezone
from .models import ApiKey, User
from audit.middleware import log_audit_event
from rigs.models import Rig, RigTag


def register_view(request):
    """User registration page. First user becomes admin automatically."""
    if request.user.is_authenticated:
        return redirect('dashboard:rig-list')

    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')

        # Validation
        if not email or not password:
            messages.error(request, 'Email and password are required')
            return render(request, 'accounts/register.html')

        if password != password_confirm:
            messages.error(request, 'Passwords do not match')
            return render(request, 'accounts/register.html')

        if len(password) < 8:
            messages.error(request, 'Password must be at least 8 characters')
            return render(request, 'accounts/register.html')

        if User.objects.filter(email=email).exists():
            messages.error(request, 'An account with this email already exists')
            return render(request, 'accounts/register.html')

        # Create user — first user becomes admin
        is_first_user = User.objects.count() == 0
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            is_staff=is_first_user,
        )

        log_audit_event(request, 'user.registered', 'User', user.id, {
            'email': email,
            'is_admin': is_first_user,
        })

        # Log the user in
        login(request, user)
        messages.success(request, 'Account created successfully!')
        return redirect('dashboard:rig-list')

    return render(request, 'accounts/register.html')


def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email', '')
        password = request.POST.get('password', '')
        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR')
            log_audit_event(request, 'user.session.login', 'User', user.id, {
                'ip': ip,
            })
            return redirect('dashboard:rig-list')
        else:
            log_audit_event(request, 'user.login.failed', 'User', None, {
                'attempted_email': email[:100],
            })
            messages.error(request, 'Invalid email or password')
    return render(request, 'accounts/login.html')


def logout_view(request):
    if request.user.is_authenticated:
        log_audit_event(request, 'user.session.logout', 'User', request.user.id, {})
    logout(request)
    return redirect('accounts:login')


@login_required
def api_keys(request):
    keys = ApiKey.objects.filter(user=request.user).annotate(
        rig_count=models.Count('enrolled_rigs')
    ).prefetch_related(
        models.Prefetch('enrolled_rigs', queryset=Rig.objects.only('uuid', 'name', 'status'))
    )
    return render(request, 'accounts/api_keys.html', {'keys': keys})


@login_required
def create_api_key(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Key name is required')
            return redirect('accounts:api-keys')

        plaintext = secrets.token_hex(32)
        key_hash = ApiKey.hash_key(plaintext)

        api_key = ApiKey.objects.create(
            user=request.user,
            name=name,
            base_name=name,
            key_hash=key_hash,
        )

        log_audit_event(request, 'apikey.created', 'ApiKey', api_key.id,
                       {'name': name, 'key_prefix': plaintext[:8], 'plaintext': plaintext})

        return render(request, 'accounts/_key_reveal.html', {
            'key': api_key,
            'plaintext': plaintext,
        })

    return redirect('accounts:api-keys')


@login_required
def revoke_api_key(request, key_id):
    if request.method == 'POST':
        key = get_object_or_404(ApiKey, id=key_id, user=request.user)
        key.is_active = False
        key.revoked_at = timezone.now()
        key.save(update_fields=['is_active', 'revoked_at'])

        log_audit_event(request, 'apikey.revoked', 'ApiKey', key.id, {'name': key.name})

        if request.headers.get('HX-Request'):
            key.rig_count = key.enrolled_rigs.count()
            return render(request, 'accounts/_key_row.html', {'key': key})
        messages.success(request, f'Key "{key.name}" revoked')
    return redirect('accounts:api-keys')


@login_required
def delete_api_key(request, key_id):
    if request.method == 'POST':
        key = get_object_or_404(ApiKey, id=key_id, user=request.user)
        if key.is_active:
            messages.error(request, 'Cannot delete an active key. Revoke it first.')
            return redirect('accounts:api-keys')
        name = key.name
        key.delete()
        log_audit_event(request, 'apikey.deleted', 'ApiKey', key_id, {'name': name})
        if request.headers.get('HX-Request'):
            return HttpResponse('')
        messages.success(request, f'Key "{name}" deleted permanently')
    return redirect('accounts:api-keys')


def _generate_transfer_name(base_name, target_user):
    """Generate a unique name for a transferred key in the target user's namespace.

    Uses base_name (always clean, never has transfer suffixes) as the starting point.
    If the target user already has a key with that name, appends an incrementing counter.
    The result is truncated to 255 chars max.
    """
    # Fallback to key's current name if base_name is empty (legacy keys)
    effective_base = base_name or 'key'

    new_name = effective_base

    # Handle collision with incrementing counter
    counter = 1
    final_name = new_name
    while ApiKey.objects.filter(user=target_user, name=final_name).exists():
        final_name = f"{effective_base}-{counter}"
        counter += 1

    # Truncate to 255 chars max (reserve space for collision suffix)
    if len(final_name) > 255:
        # Reserve for "-999" = 4 chars
        base_truncated = effective_base[:255 - 4]
        final_name = base_truncated
        counter = 1
        while ApiKey.objects.filter(user=target_user, name=final_name).exists():
            final_name = f"{base_truncated}-{counter}"
            counter += 1

    return final_name[:255]


@login_required
def admin_transfer_keys(request):
    if not request.user.is_staff:
        messages.error(request, 'Only staff users can transfer API keys.')
        return redirect('accounts:api-keys')

    source_user_id = request.GET.get('source_user_id') or request.POST.get('source_user_id')
    source_user = None
    source_keys = None

    # Step 1: Source user selected — load their keys
    if source_user_id:
        source_user = get_object_or_404(User, id=source_user_id)
        source_keys = ApiKey.objects.filter(user=source_user).annotate(
            rig_count=models.Count('enrolled_rigs')
        ).prefetch_related(
            models.Prefetch('enrolled_rigs', queryset=Rig.objects.only('uuid', 'name', 'status'))
        ).order_by('-created_at')

    # Step 2: Transfer submitted
    if request.method == 'POST' and source_user:
        key_ids = request.POST.getlist('key_ids')
        target_user_id = request.POST.get('target_user_id')

        if not key_ids:
            messages.error(request, 'Select at least one key to transfer.')
        elif not target_user_id:
            messages.error(request, 'Select a target user.')
        else:
            target_user = get_object_or_404(User, id=target_user_id)

            if target_user == source_user:
                messages.error(request, 'Cannot transfer to the same user.')
            else:
                # Re-fetch keys and verify they still belong to source user (concurrency protection)
                keys = ApiKey.objects.filter(id__in=key_ids, user=source_user)
                transferred = 0

                for key in keys:
                    new_name = _generate_transfer_name(key.base_name, target_user)

                    old_user = key.user
                    key.user = target_user
                    key.name = new_name
                    key.transfer_count = key.transfer_count + 1
                    key.save(update_fields=['user', 'name', 'transfer_count'])

                    # Update rig ownership and clear tags (tags are per-user)
                    rigs = Rig.objects.filter(enrolled_by_api_key=key)
                    rig_count = rigs.update(owner=target_user)
                    for rig in rigs:
                        rig.tags.clear()

                    log_audit_event(request, 'apikey.transferred', 'ApiKey', key.id, {
                        'name': key.name,
                        'from_user': old_user.id,
                        'from_user_email': old_user.email,
                        'to_user': target_user.id,
                        'to_user_email': target_user.email,
                        'rig_count': rig_count,
                    })
                    transferred += 1

                messages.success(
                    request,
                    f'Transferred {transferred} key(s) from {source_user.email} to {target_user.email}.'
                )
                return redirect('accounts:admin-transfer-keys')

    all_users = User.objects.order_by('email')
    return render(request, 'accounts/admin_transfer_keys.html', {
        'all_users': all_users,
        'source_user': source_user,
        'source_keys': source_keys,
    })


@login_required
def reactivate_api_key(request, key_id):
    if request.method == 'POST':
        key = get_object_or_404(ApiKey, id=key_id, user=request.user)
        if key.is_active:
            messages.error(request, 'Key is already active.')
            return redirect('accounts:api-keys')
        key.is_active = True
        key.revoked_at = None
        key.save(update_fields=['is_active', 'revoked_at'])
        log_audit_event(request, 'apikey.reactivated', 'ApiKey', key.id, {'name': key.name})
        if request.headers.get('HX-Request'):
            key.rig_count = key.enrolled_rigs.count()
            return render(request, 'accounts/_key_row.html', {'key': key})
        messages.success(request, f'Key "{key.name}" reactivated')
    return redirect('accounts:api-keys')


@login_required
def profile_view(request):
    """User profile page — view info, change password, configure power settings."""
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'change_password':
            current = request.POST.get('current_password', '')
            new = request.POST.get('new_password', '')
            confirm = request.POST.get('confirm_password', '')

            if not request.user.check_password(current):
                messages.error(request, 'Current password is incorrect')
            elif new != confirm:
                messages.error(request, 'New passwords do not match')
            elif len(new) < 8:
                messages.error(request, 'Password must be at least 8 characters')
            else:
                request.user.set_password(new)
                request.user.save()
                log_audit_event(request, 'user.password_changed', 'User', request.user.id, {})
                messages.success(request, 'Password changed successfully')
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, request.user)

        elif action == 'update_power_settings':
            try:
                rate = request.POST.get('electricity_rate_kwh', '').strip()
                if rate:
                    rate_val = float(rate)
                    if rate_val < 0 or rate_val > 10:
                        messages.error(request, 'Electricity rate must be between 0 and 10 $/kWh')
                    else:
                        request.user.electricity_rate_kwh = rate_val
                        request.user.save(update_fields=['electricity_rate_kwh'])
                        log_audit_event(request, 'user.power_settings_changed', 'User', request.user.id, {'rate': rate_val})
                        messages.success(request, 'Power settings updated successfully')
                else:
                    request.user.electricity_rate_kwh = Decimal('0.3300')
                    request.user.save(update_fields=['electricity_rate_kwh'])
                    log_audit_event(request, 'user.power_settings_changed', 'User', request.user.id, {'rate': Decimal('0.3300')})
                    messages.success(request, 'Power settings reset to default (0.3300 $/kWh)')
            except (ValueError, TypeError):
                messages.error(request, 'Invalid electricity rate format — please enter a number (e.g. 0.12)')

    return render(request, 'accounts/profile.html')


# ── Tag CRUD (no HTMX, plain form posts) ──────────────────────────────────

@login_required
def tags(request):
    """List all tags for the current user."""
    user_tags = RigTag.objects.filter(user=request.user).order_by('name')
    return render(request, 'accounts/tags.html', {'tags': user_tags})


@login_required
def create_tag(request):
    """Create a new tag. Plain form POST, redirect on success."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        color = request.POST.get('color', '#6B7280').strip()
        if not name:
            messages.error(request, 'Tag name is required')
        elif not color.startswith('#') or len(color) != 7:
            messages.error(request, 'Invalid color format')
        else:
            tag, created = RigTag.objects.get_or_create(
                user=request.user,
                name=name[:100],
                defaults={'color': color},
            )
            if created:
                log_audit_event(request, 'tag.created', 'RigTag', tag.id,
                                {'name': tag.name, 'color': tag.color})
                messages.success(request, f'Tag "{tag.name}" created')
            else:
                messages.error(request, f'Tag "{name}" already exists')
    return redirect('accounts:tags')


@login_required
def update_tag(request, tag_id):
    """Update an existing tag. Plain form POST, redirect on success."""
    if request.method == 'POST':
        tag = get_object_or_404(RigTag, id=tag_id, user=request.user)
        name = request.POST.get('name', '').strip()
        color = request.POST.get('color', '').strip()
        if name:
            tag.name = name[:100]
        if color and color.startswith('#') and len(color) == 7:
            tag.color = color
        tag.save(update_fields=['name', 'color'])
        log_audit_event(request, 'tag.updated', 'RigTag', tag.id,
                        {'name': tag.name, 'color': tag.color})
        messages.success(request, f'Tag "{tag.name}" updated')
    return redirect('accounts:tags')


@login_required
def delete_tag(request, tag_id):
    """Delete a tag. Plain form POST, redirect on success."""
    if request.method == 'POST':
        tag = get_object_or_404(RigTag, id=tag_id, user=request.user)
        log_audit_event(request, 'tag.deleted', 'RigTag', tag.id, {'name': tag.name})
        tag.delete()
        messages.success(request, f'Tag "{tag.name}" deleted')
    return redirect('accounts:tags')
