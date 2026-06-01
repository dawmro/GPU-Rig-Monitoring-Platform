from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('dashboard/rigs/', views.rig_list, name='rig-list'),
    path('dashboard/rigs/<str:uuid>/', views.rig_detail, name='rig-detail'),
    path('dashboard/rigs/<str:uuid>/rename/', views.rig_rename, name='rig-rename'),
    path('dashboard/rigs/<str:uuid>/htmx-metrics/', views.htmx_metrics, name='htmx-metrics'),
]
