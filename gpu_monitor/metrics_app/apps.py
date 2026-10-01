from django.apps import AppConfig


class MetricsAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'metrics_app'
    verbose_name = 'Metrics'

    def ready(self):
        # Import the checks module so its @register-decorated system
        # checks are registered at app-ready time (required for
        # `manage.py check` to discover and run them). Mirrors
        # dashboard/apps.py. Without this the module is never imported
        # on a clean django.setup(), so none of the metrics_app defense
        # checks (Phase 1/2/subvendor/storage/has_active_job/uuid) ever
        # register and `manage.py check` reports 0 issues (false comfort).
        from . import checks  # noqa: F401
