from django.urls import path
from . import views

app_name = 'metrics_app'

urlpatterns = [
    path('api/v1/ingest/', views.IngestView.as_view(), name='ingest'),
    path('api/v1/health/', views.HealthView.as_view(), name='health'),
    path('api/v1/rigs/<str:uuid>/metrics/', views.RigMetricsView.as_view(), name='rig-metrics'),
    path('api/v1/rigs/<str:uuid>/chart-data/', views.ChartDataView.as_view(), name='chart-data'),
]
