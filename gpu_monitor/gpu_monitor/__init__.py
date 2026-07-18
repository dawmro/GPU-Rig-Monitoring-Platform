"""
Django project initialization.

Imports the Celery app to ensure it's loaded when Django starts.
This makes the `celery` command work and enables @shared_task decorators.
"""

from .celery import app as celery_app

__all__ = ('celery_app',)
