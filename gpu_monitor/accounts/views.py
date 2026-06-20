import secrets
from django.db import models
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from .models import ApiKey, User
from audit.middleware import log_audit_event
from rigs.models import RigTag


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
            return redirect('dashboard:rig-list')
        else:
            messages.error(request, 'Invalid email or password')
    return render(request, 'accounts/login.html')


def logout_view(request):
    logout(request)
    return redirect('accounts:login')


@login_required
def api_keys(request):
    keys = ApiKey.objects.filter(user=request.user).annotate(
        rig_count=models.Count('enrolled_rigs')
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
            key_hash=key_hash,
        )

        log_audit_event(request, 'apikey.created', 'ApiKey', api_key.id,
                       {'key_prefix': plaintext[:8]})

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

        log_audit_event(request, 'apikey.revoked', 'ApiKey', key.id, {})

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
        log_audit_event(request, 'apikey.reactivated', 'ApiKey', key.id, {})
        if request.headers.get('HX-Request'):
            key.rig_count = key.enrolled_rigs.count()
            return render(request, 'accounts/_key_row.html', {'key': key})
        messages.success(request, f'Key "{key.name}" reactivated')
    return redirect('accounts:api-keys')


@login_required
def profile_view(request):
    """User profile page — view info and change password."""
    if request.method == 'POST':
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
