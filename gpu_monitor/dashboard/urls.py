from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index_view, name='index'),
    path('dashboard/rigs/', views.rig_list, name='rig-list'),
    path('dashboard/rigs/<str:uuid>/', views.rig_detail, name='rig-detail'),
    path('dashboard/rigs/<str:uuid>/rename/', views.rig_rename, name='rig-rename'),
    path('dashboard/rigs/<str:uuid>/delete/', views.rig_delete, name='rig-delete'),
    path('dashboard/rigs/<str:uuid>/htmx-metrics/', views.htmx_metrics, name='htmx-metrics'),
    path('dashboard/rigs/<str:uuid>/htmx-status/', views.htmx_rig_status, name='htmx-rig-status'),
    path('dashboard/rigs/<str:uuid>/tags/<str:tag_id>/toggle/', views.rig_toggle_tag, name='rig-toggle-tag'),
]
