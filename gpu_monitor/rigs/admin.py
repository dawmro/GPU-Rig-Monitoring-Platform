from django.contrib import admin
from .models import Rig, RigTag


@admin.register(Rig)
class RigAdmin(admin.ModelAdmin):
    list_display = ('name', 'uuid', 'owner', 'status', 'last_seen', 'expected_gpus')
    list_filter = ('status',)
    search_fields = ('name', 'uuid')
    readonly_fields = ('uuid', 'created_at')


@admin.register(RigTag)
class RigTagAdmin(admin.ModelAdmin):
    list_display = ('name', 'color', 'user')
    list_filter = ('user',)
