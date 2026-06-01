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
