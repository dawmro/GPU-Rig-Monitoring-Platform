from django.contrib import admin
from django.shortcuts import render, redirect
from django.contrib import messages
from django.urls import path
from django.utils.html import format_html
from .models import ApiKey, User
from rigs.models import Rig
from django.db import models


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'is_active', 'rig_count_col', 'transfer_count', 'created_at']
    list_filter = ['is_active', 'user']
    search_fields = ['name', 'base_name', 'user__email']
    readonly_fields = ['key_hash', 'created_at', 'last_used_at', 'revoked_at', 'transfer_count']

    def rig_count_col(self, obj):
        return obj.enrolled_rigs.count()
    rig_count_col.short_description = 'Rigs'

    def get_urls(self):
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

        if not key:
            messages.error(request, 'API key not found.')
            return redirect('admin:accounts_apikey_changelist')

        if request.method == 'POST':
            target_user_id = request.POST.get('target_user')
            if not target_user_id:
                messages.error(request, 'Select a target user.')
            else:
                try:
                    target_user = User.objects.get(id=target_user_id)
                except User.DoesNotExist:
                    messages.error(request, 'Target user not found.')
                else:
                    if target_user == key.user:
                        messages.error(request, 'Cannot transfer to the same user.')
                    else:
                        from .views import _generate_transfer_name

                        old_user = key.user
                        new_name = _generate_transfer_name(key.base_name, target_user)

                        key.user = target_user
                        key.name = new_name
                        key.transfer_count = key.transfer_count + 1
                        key.save(update_fields=['user', 'name', 'transfer_count'])

                        # CRITICAL: Update rig ownership for all enrolled rigs
                        rig_count = Rig.objects.filter(
                            enrolled_by_api_key=key
                        ).update(owner=target_user)

                        messages.success(
                            request,
                            f'Key "{key.name}" transferred from {old_user.email} '
                            f'to {target_user.email}. {rig_count} rig(s) affected.'
                        )
                        return redirect('admin:accounts_apikey_changelist')

        users = User.objects.exclude(id=key.user.id).order_by('email')
        enrolled_rigs = key.enrolled_rigs.all()

        return render(request, 'admin/accounts/apikey/transfer.html', {
            'key': key,
            'users': users,
            'enrolled_rigs': enrolled_rigs,
            'title': f'Transfer API Key: {key.name}',
        })