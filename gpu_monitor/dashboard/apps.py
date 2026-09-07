from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dashboard'
    verbose_name = 'Dashboard'

    def ready(self):
        # Import the checks module to register @register-decorated
        # system checks. Django's check framework discovers them at
        # `manage.py check` time, but the registration side effect
        # must happen at app-ready time, not at import time.
        from . import checks  # noqa: F401
