#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def patch_logging():
    """Patch Django settings LOGGING to avoid file permission issues.

    The default settings.py LOGGING config writes to logs/app.log which
    may not be writable by all users. This function checks if the log
    file is writable and removes the file handler if not.
    """
    try:
        from django.conf import settings
        log_file = settings.BASE_DIR / 'logs' / 'app.log'
        if log_file.exists():
            try:
                with open(str(log_file), 'a'):
                    pass
            except (OSError, PermissionError):
                # Log file not writable — use console-only logging
                settings.LOGGING = {
                    'version': 1,
                    'disable_existing_loggers': True,
                    'handlers': {
                        'console': {'class': 'logging.StreamHandler', 'stream': sys.stderr},
                    },
                    'root': {'handlers': ['console'], 'level': 'WARNING'},
                }
    except Exception:
        pass  # Ignore any errors — logging is not critical


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gpu_monitor.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    patch_logging()
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
