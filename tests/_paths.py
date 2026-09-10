"""Shared path constants for test scripts.

This module exists so the 5+ test scripts in tests/ all agree on
where the gpu_monitor/ Django project root is. The alternative is
to duplicate `Path(__file__).resolve().parent.parent / 'gpu_monitor'`
in every script, which is fragile (a moved file breaks all of them
silently) and ugly.

Importing from this module is the contract:

    from tests._paths import GPU_MONITOR_DIR
    sys.path.insert(0, str(GPU_MONITOR_DIR))

PROJECT_ROOT and GPU_MONITOR_DIR are the only exports.
"""
from pathlib import Path

# workspace/ — where this tests/ directory lives, one level up
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# workspace/gpu_monitor/ — the Django project root (where manage.py
# lives). This is the path that all test scripts add to sys.path
# so they can `import django` and `from metrics_app.models import ...`.
GPU_MONITOR_DIR = PROJECT_ROOT / 'gpu_monitor'
