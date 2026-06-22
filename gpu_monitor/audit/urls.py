from django.urls import path
from . import views

app_name = 'audit'

urlpatterns = [
    path('accounts/audit-log/', views.audit_log_view, name='audit-log'),
]
