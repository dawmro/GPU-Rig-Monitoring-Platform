"""
Celery application for GPU Rig Monitoring Platform.

This module creates the Celery app instance and configures it from Django settings.
The app is imported in gpu_monitor/__init__.py to ensure it's loaded on Django startup.
"""

import os
from celery import Celery
from celery.schedules import crontab

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gpu_monitor.settings')

app = Celery('gpu_monitor')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Optional: Beat schedule can be defined here OR in Django admin via django-celery-beat.
# We use DatabaseScheduler (django-celery-beat) so schedule is managed in admin.
# Example of hardcoded schedule (not used when DatabaseScheduler is active):
# app.conf.beat_schedule = {
#     'update-rig-status-every-2-minutes': {
#         'task': 'rigs.tasks.update_rig_status',
#         'schedule': crontab(minute='*/2'),
#         'options': {'queue': 'maintenance', 'priority': 5},
#     },
# }

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debug task for testing worker connectivity."""
    print(f'Request: {self.request!r}')
