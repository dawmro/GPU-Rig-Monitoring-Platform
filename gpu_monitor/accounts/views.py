import secrets
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from .models import ApiKey, User
from audit.middleware import log_audit_event


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
    keys = ApiKey.objects.filter(user=request.user)
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
        key.save(update_fields=['is_active'])

        log_audit_event(request, 'apikey.revoked', 'ApiKey', key.id, {})

        if request.headers.get('HX-Request'):
            return HttpResponse('')
        messages.success(request, f'Key "{key.name}" revoked')
    return redirect('accounts:api-keys')


# ── Tag CRUD ──────────────────────────────────────────────────────────────

from rigs.models import RigTag
from django.http import Http404

@login_required
def tags(request):
    """List all tags for the current user."""
    user_tags = RigTag.objects.filter(user=request.user).order_by('name')
    return render(request, 'accounts/tags.html', {'tags': user_tags})


@login_required
def create_tag(request):
    """Create a new tag."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        color = request.POST.get('color', '#6B7280').strip()
        if not name:
            messages.error(request, 'Tag name is required')
            return redirect('accounts:tags')
        if not color.startswith('#') or len(color) != 7:
            color = '#6B7280'
        tag, created = RigTag.objects.get_or_create(
            user=request.user,
            name=name[:100],
            defaults={'color': color},
        )
        if not created:
            messages.error(request, f'Tag "{name}" already exists')
        else:
            log_audit_event(request, 'tag.created', 'RigTag', tag.id, {'name': tag.name, 'color': tag.color})
        if request.headers.get('HX-Request'):
            # Re-render the entire tag list so the new tag appears
            user_tags = RigTag.objects.filter(user=request.user).order_by('name')
            return render(request, 'accounts/_tag_list.html', {'tags': user_tags})
    return redirect('accounts:tags')


@login_required
def update_tag(request, tag_id):
    """Update an existing tag (name and color)."""
    if request.method == 'POST':
        tag = get_object_or_404(RigTag, id=tag_id, user=request.user)
        name = request.POST.get('name', '').strip()
        color = request.POST.get('color', '').strip()
        if name:
            tag.name = name[:100]
        if color and color.startswith('#') and len(color) == 7:
            tag.color = color
        tag.save(update_fields=['name', 'color'])
        log_audit_event(request, 'tag.updated', 'RigTag', tag.id, {'name': tag.name, 'color': tag.color})
        if request.headers.get('HX-Request'):
            return render(request, 'accounts/_tag_row.html', {'tag': tag})
    return redirect('accounts:tags')


@login_required
def delete_tag(request, tag_id):
    """Delete a tag."""
    if request.method == 'POST':
        tag = get_object_or_404(RigTag, id=tag_id, user=request.user)
        log_audit_event(request, 'tag.deleted', 'RigTag', tag.id, {'name': tag.name})
        tag.delete()
        if request.headers.get('HX-Request'):
            return HttpResponse('')
    return redirect('accounts:tags')
