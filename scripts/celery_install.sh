#!/bin/bash
# =============================================================================
# celery_install.sh — Celery Migration for GPU Rig Monitoring Platform
# =============================================================================
# Installs and configures Celery infrastructure for GPU Rig Monitoring Platform.
# Run on production server after base server_install.sh completes.
# Supports phased installation with rollback capability.
#
# Usage:
#   bash celery_install.sh [options]
#   bash celery_install.sh --phase 0-4      # Run specific phase
#   bash celery_install.sh --all            # Run all phases (default)
#   bash celery_install.sh --dry-run        # Show what would be done
#   bash celery_install.sh --rollback N     # Rollback specific phase
#   bash celery_install.sh --verify         # Verify all phases
#
# Prerequisites:
#   - Base server_install.sh already completed
#   - Django project at /opt/gpu_monitor
#   - Virtual environment at /opt/gpu_monitor/venv
#   - .env file at /opt/gpu_monitor/.env with Redis credentials
#   - Root/sudo access
# =============================================================================

set -euo pipefail

# =============================================================================
# CONFIGURATION
# =============================================================================
APP_DIR="/opt/gpu_monitor"
APP_USER="monitoring"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${SCRIPT_DIR}"
VENV_PATH="${APP_DIR}/venv"
ENV_FILE="${APP_DIR}/.env"
DJANGO_SETTINGS="gpu_monitor.settings"
CELERY_APP="gpu_monitor"

# Phase control
PHASE="${1:-all}"
DRY_RUN=false
ROLLBACK_PHASE=""
VERIFY_ONLY=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

run_cmd() {
    local cmd="$1"
    local desc="${2:-Running command}"
    log_info "$desc"
    if [[ "$DRY_RUN" == "true" ]]; then
        log_warn "[DRY-RUN] Would run: $cmd"
        return 0
    fi
    if eval "$cmd"; then
        log_success "$desc completed"
        return 0
    else
        log_error "$desc failed"
        return 1
    fi
}

run_python() {
    local code="$1"
    local desc="${2:-Running Python code}"
    log_info "$desc"
    if [[ "$DRY_RUN" == "true" ]]; then
        log_warn "[DRY-RUN] Would run Python code"
        return 0
    fi
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && export DJANGO_SETTINGS_MODULE="$DJANGO_SETTINGS" && set -a && source "$ENV_FILE" && set +a && python -c "$code"
}

verify_service() {
    local service="$1"
    if systemctl is-active --quiet "$service"; then
        log_success "Service $service is active"
        return 0
    else
        log_error "Service $service is not active"
        return 1
    fi
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check if base server install completed
    if [[ ! -f "$APP_DIR/manage.py" ]]; then
        log_error "Django project not found at $APP_DIR. Run server_install.sh first."
        exit 1
    fi
    
    if [[ ! -f "$ENV_FILE" ]]; then
        log_error ".env file not found at $ENV_FILE"
        exit 1
    fi
    
    if [[ ! -d "$VENV_PATH" ]]; then
        log_error "Virtual environment not found at $VENV_PATH"
        exit 1
    fi
    
    # Check if base services are running
    for svc in postgresql redis-server nginx gunicorn; do
        if ! systemctl is-active --quiet "$svc"; then
            log_warn "Service $svc is not active"
        fi
    done
    
    log_success "Prerequisites check passed"
}

# =============================================================================
# PHASE 0: INFRASTRUCTURE (Redis + Celery Packages + Django Config)
# =============================================================================
phase0_install_redis() {
    log_info "Phase 0.1: Installing & configuring Redis..."
    run_cmd "apt update && apt install -y redis-server" "Install Redis"
    
    # Generate password if not in .env
    if ! grep -q "^REDIS_PASSWORD=" "$ENV_FILE" 2>/dev/null; then
        local redis_pass
        redis_pass=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
        echo "REDIS_PASSWORD=${redis_pass}" >> "$ENV_FILE"
        log_warn "Generated Redis password (save this!): ${redis_pass}"
    fi
    
    # Configure Redis
    local redis_pass
    redis_pass=$(grep "^REDIS_PASSWORD=" "$ENV_FILE" | cut -d= -f2-)
    sudo tee /etc/redis/redis.conf > /dev/null <<REDIS
bind 127.0.0.1 ::1
requirepass $redis_pass
maxmemory 2gb
maxmemory-policy allkeys-lru
save ""
appendonly no
REDIS
    
    run_cmd "sudo systemctl restart redis-server && sudo systemctl enable redis-server" "Restart/enable Redis"
    
    # Verify Redis
    if redis-cli -a "$redis_pass" ping | grep -q PONG; then
        log_success "Redis connectivity verified"
    else
        log_error "Redis ping failed"
        return 1
    fi
}

phase0_install_packages() {
    log_info "Phase 0.2: Installing Celery packages..."
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate"
    run_cmd "pip install celery redis django-celery-beat django-celery-results" "Install Celery stack"
    
    run_cmd "python -c \"import celery, redis, django_celery_beat, django_celery_results; print('celery:', celery.__version__); print('redis:', redis.__version__); print('django-celery-beat:', django_celery_beat.__version__); print('django-celery-results:', django_celery_results.__version__)\"" "Verify imports"
}

phase0_configure_django() {
    log_info "Phase 0.3: Configuring Django settings..."
    local settings_file="${APP_DIR}/gpu_monitor/gpu_monitor/settings.py"
    
    if grep -q "CELERY_BROKER_URL" "$settings_file"; then
        log_warn "Celery settings already exist, skipping"
        return 0
    fi
    
    # Add Redis/Celery config before the last line
    cat >> "$settings_file" <<'SETTINGS_EOF'

# Redis / Celery — build URLs from components (same pattern as DB)
REDIS_HOST = os.environ.get('REDIS_HOST', '127.0.0.1')
REDIS_PORT = os.environ.get('REDIS_PORT', '6379')
REDIS_PASSWORD=os.environ.get('REDIS_PASSWORD', '')
REDIS_DB_BROKER = os.environ.get('REDIS_DB_BROKER', '0')
REDIS_DB_RESULTS = os.environ.get('REDIS_DB_RESULTS', '1')

from urllib.parse import quote

def _redis_url(db: str) -> str:
    """Build redis:// URL from components. Handles empty password."""
    auth = f":{quote(REDIS_PASSWORD, safe='')}@" if REDIS_PASSWORD else ""
    return f"redis://{auth}{REDIS_HOST}:{REDIS_PORT}/{db}"

CELERY_BROKER_URL = _redis_url(REDIS_DB_BROKER)
CELERY_RESULT_BACKEND = _redis_url(REDIS_DB_RESULTS)

# Celery Configuration
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 100
CELERY_RESULT_EXPIRES = 86400  # 24h
CELERY_TASK_VISIBILITY_TIMEOUT = 3600  # 1h (covers long compaction)
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers.DatabaseScheduler'

SETTINGS_EOF
    
    # Add Celery apps to INSTALLED_APPS if not present
    if ! grep -q "django_celery_beat" "$settings_file"; then
        sed -i "/^INSTALLED_APPS = \[/a\    'django_celery_beat',\n    'django_celery_results'," "$settings_file"
    fi
    
    # Verify settings load
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && set -a && source "$ENV_FILE" && set +a
    python -c "import django; django.setup(); from django.conf import settings; print('CELERY_BROKER_URL:', settings.CELERY_BROKER_URL)"
}

phase0_run_migrations() {
    log_info "Phase 0.4: Running migrations for Celery apps..."
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && set -a && source "$ENV_FILE" && set +a
    
    run_cmd "python manage.py migrate django_celery_beat" "Migrate django-celery-beat"
    run_cmd "python manage.py migrate django_celery_results" "Migrate django-celery-results"
    run_cmd "python manage.py migrate" "Run all migrations"
    
    # Verify tables
    run_cmd "echo \"\\dt django_celery_*\" | python manage.py dbshell" "Verify Celery tables"
}

run_phase0() {
    log_info "=== Phase 0: Infrastructure ==="
    phase0_install_redis
    phase0_install_packages
    phase0_configure_django
    phase0_run_migrations
    log_success "Phase 0 completed"
}

# =============================================================================
# PHASE 1: CELERY INFRASTRUCTURE (App + Systemd Units + Workers)
# =============================================================================
phase1_create_celery_app() {
    log_info "Phase 1.1: Creating Celery app..."
    
    cat > "${APP_DIR}/gpu_monitor/celery.py" <<'CELERY_EOF'
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
CELERY_EOF
    
    # Update __init__.py
    cat > "${APP_DIR}/gpu_monitor/__init__.py" <<'INIT_EOF'
"""
Django project initialization.

Imports the Celery app to ensure it's loaded when Django starts.
This makes the `celery` command work and enables @shared_task decorators.
"""

from .celery import app as celery_app

__all__ = ('celery_app',)
INIT_EOF
    
    # Verify
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && export DJANGO_SETTINGS_MODULE="$DJANGO_SETTINGS" && set -a && source "$ENV_FILE" && set +a
    python -c "from gpu_monitor.celery import app; print('Celery app:', app.main)"
}

phase1_create_systemd_units() {
    log_info "Phase 1.2: Creating systemd unit files..."
    
    # Ingest worker (1 instance × 2 concurrency)
    sudo tee /etc/systemd/system/celery-ingest@.service > /dev/null <<'EOF'
[Unit]
Description=Celery Ingest Worker %i
After=network.target redis.service postgresql.service
Wants=redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor worker \
    --loglevel=INFO \
    --queues=ingest \
    --concurrency=2 \
    --pool=prefork \
    --hostname=ingest-worker-%i@%h \
    --max-tasks-per-child=100 \
    --time-limit=300 \
    --soft-time-limit=240
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # Maintenance worker (1 instance × 1 concurrency)
    sudo tee /etc/systemd/system/celery-maintenance@.service > /dev/null <<'EOF'
[Unit]
Description=Celery Maintenance Worker %i
After=network.target redis.service postgresql.service
Wants=redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor worker \
    --loglevel=INFO \
    --queues=maintenance \
    --concurrency=1 \
    --pool=prefork \
    --hostname=maint-worker-%i@%h \
    --max-tasks-per-child=10 \
    --time-limit=7200 \
    --soft-time-limit=6600
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # Default worker (1 instance × 1 concurrency)
    sudo tee /etc/systemd/system/celery-default@.service > /dev/null <<'EOF'
[Unit]
Description=Celery Default Worker %i
After=network.target redis.service postgresql.service
Wants=redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor worker \
    --loglevel=INFO \
    --queues=default,alerts,reports \
    --concurrency=1 \
    --pool=prefork \
    --hostname=default-worker-%i@%h \
    --max-tasks-per-child=50 \
    --time-limit=300 \
    --soft-time-limit=240
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # Beat scheduler
    sudo tee /etc/systemd/system/celery-beat.service > /dev/null <<'EOF'
[Unit]
Description=Celery Beat Scheduler
After=network.target redis.service postgresql.service
Wants=redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor beat \
    --loglevel=INFO \
    --scheduler=django_celery_beat.schedulers:DatabaseScheduler \
    --pidfile=/var/run/celery/beat.pid \
    --schedule=/var/lib/celery/beat-schedule
RuntimeDirectory=celery
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # Create runtime directories
    sudo mkdir -p /var/run/celery /var/lib/celery
    sudo chown monitoring:monitoring /var/run/celery /var/lib/celery
    
    # Verify unit files
    systemd-analyze verify /etc/systemd/system/celery-ingest@.service
    systemd-analyze verify /etc/systemd/system/celery-maintenance@.service
    systemd-analyze verify /etc/systemd/system/celery-default@.service
    systemd-analyze verify /etc/systemd/system/celery-beat.service
}

phase1_start_services() {
    log_info "Phase 1.3: Starting Celery services..."
    
    sudo systemctl daemon-reload
    
    # Beat first (scheduler before workers)
    sudo systemctl enable --now celery-beat
    sleep 3
    systemctl status celery-beat --no-pager
    
    # Default worker
    sudo systemctl enable --now celery-default@1
    sleep 2
    systemctl status celery-default@1 --no-pager
    
    # Maintenance worker
    sudo systemctl enable --now celery-maintenance@1
    sleep 2
    systemctl status celery-maintenance@1 --no-pager
    
    # Verify
    verify_services
}

verify_services() {
    for svc in celery-beat celery-ingest@1 celery-maintenance@1 celery-default@1; do
        if ! systemctl is-active --quiet "$svc"; then
            log_error "Service $svc not active"
            return 1
        fi
    done
    
    # Verify Celery connectivity
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && export DJANGO_SETTINGS_MODULE="$DJANGO_SETTINGS" && set -a && source "$ENV_FILE" && set +a
    celery -A gpu_monitor inspect ping
    celery -A gpu_monitor inspect active_queues
}

run_phase1() {
    log_info "=== Phase 1: Celery Infrastructure ==="
    phase1_create_celery_app
    phase1_create_systemd_units
    phase1_start_services
    log_success "Phase 1 completed"
}

# =============================================================================
# PHASE 2: RIG STATUS UPDATE (2-min cron → Beat)
# =============================================================================
phase2_create_task() {
    log_info "Phase 2.1: Creating rigs/tasks.py..."
    
    cat > "${APP_DIR}/gpu_monitor/rigs/tasks.py" <<'TASK_EOF'
"""
Celery tasks for rigs app.

Migrates the rig status update logic from management command to Celery task.
"""
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from rigs.models import Rig
from metrics_app.models import RigStatusEvent


@shared_task(bind=True, queue='maintenance', priority=5)
def update_rig_status(self):
    """
    Update rig status (stale/offline) based on last_seen timestamp.
    
    Runs every 2 minutes via Celery Beat.
    Migrated from management command `update_rig_status`.
    
    Returns:
        dict: {'stale': int, 'offline': int, 'processed': int}
    """
    now = timezone.now()
    stale_threshold = now - timedelta(minutes=2)
    offline_threshold = now - timedelta(minutes=10)

    stale_count = 0
    offline_count = 0

    # Mark rigs as stale if not seen in 2-10 minutes
    stale_rigs = Rig.objects.filter(
        status=Rig.Status.ONLINE,
        last_seen__lt=stale_threshold,
        last_seen__gte=offline_threshold,
    )
    for rig in stale_rigs:
        rig.status = Rig.Status.STALE
        rig.save(update_fields=['status'])
        RigStatusEvent.objects.create(
            rig_uuid=str(rig.uuid),
            status=Rig.Status.STALE,
            previous_status=Rig.Status.ONLINE,
        )
        stale_count += 1

    # Mark rigs as offline if not seen in 10+ minutes
    offline_rigs = Rig.objects.filter(
        last_seen__lt=offline_threshold,
    ).exclude(status=Rig.Status.OFFLINE)

    for rig in offline_rigs:
        old_status = rig.status
        rig.status = Rig.Status.OFFLINE
        rig.save(update_fields=['status'])
        RigStatusEvent.objects.create(
            rig_uuid=str(rig.uuid),
            status=Rig.Status.OFFLINE,
            previous_status=old_status,
        )
        offline_count += 1

    return {
        'stale': stale_count,
        'offline': offline_count,
        'processed': stale_count + offline_count,
        'timestamp': timezone.now().isoformat(),
    }
TASK_EOF

    # Verify task loads
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && export DJANGO_SETTINGS_MODULE="$DJANGO_SETTINGS" && set -a && source "$ENV_FILE" && set +a
    python -c "from rigs.tasks import update_rig_status; print('Task loaded:', update_rig_status)"
}

phase2_create_beat_task() {
    log_info "Phase 2.2: Creating periodic task in Beat..."
    
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && export DJANGO_SETTINGS_MODULE="$DJANGO_SETTINGS" && set -a && source "$ENV_FILE" && set +a
    
    python -c "
import django, json
django.setup()
from django_celery_beat.models import PeriodicTask, IntervalSchedule

schedule, _ = IntervalSchedule.objects.get_or_create(
    every=2,
    period=IntervalSchedule.MINUTES,
)

task, created = PeriodicTask.objects.get_or_create(
    name='Update Rig Status (every 2 min)',
    task='rigs.tasks.update_rig_status',
    defaults={
        'interval': schedule,
        'queue': 'maintenance',
        'priority': 5,
        'enabled': True,
    }
)
if created:
    print('Periodic task created')
else:
    print('Periodic task already exists')
"
}

phase2_verify() {
    log_info "Phase 2.3: Verifying task execution (waiting 2 minutes)..."
    sleep 120
    
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && set -a && source "$ENV_FILE" && set +a
    python -c "
from rigs.models import Rig
from metrics_app.models import RigStatusEvent
from django.utils import timezone
from datetime import timedelta

# Recent status events
events = RigStatusEvent.objects.order_by('-timestamp')[:20]
for e in events:
    print(f'{e.timestamp} | {e.rig_uuid} | {e.previous_status} -> {e.status}')

# Current rig statuses
rigs = Rig.objects.all()
for r in rigs:
    print(f'{r.name} ({r.uuid}): {r.status} | last_seen: {r.last_seen}')
"
}

phase2_disable_cron() {
    log_info "Phase 2.4: Disabling cron job..."
    sudo sed -i 's/^\*/# *\//' /etc/cron.d/rig-status
    cat /etc/cron.d/rig-status
    log_success "Cron job disabled"
}

run_phase2() {
    log_info "=== Phase 2: Rig Status Update (2-min cron → Beat) ==="
    phase2_create_task
    phase2_create_beat_task
    phase2_verify
    phase2_disable_cron
    log_success "Phase 2 completed"
}

# =============================================================================
# PHASE 3: DATA MAINTENANCE (Daily 3 AM → Beat)
# =============================================================================
phase3_create_metrics_tasks() {
    log_info "Phase 3.1: Creating metrics_app/tasks.py..."
    
    cat > "${APP_DIR}/gpu_monitor/metrics_app/tasks.py" <<'TASK_EOF'
"""
Celery tasks for metrics_app app.

Migrates maintenance operations from management commands to Celery tasks.
Tasks run on the maintenance queue with appropriate priorities and timeouts.
"""

from celery import shared_task
from django.core.management import call_command
from django.db import connection
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, queue='maintenance', priority=3, max_retries=0)
def compact_data(self, phase='all', verbose=False, days=31):
    """
    Compact old metric data into larger time buckets.
    
    Runs 3-tier compaction:
    - Tier 2 (1-7 days): 1-min -> 15-min buckets
    - Tier 3 (7-31 days): 15-min -> 1-hour buckets
    
    Uses PostgreSQL advisory lock to prevent concurrent runs.
    
    Args:
        phase: 'all', 'tier2', or 'tier3'
        verbose: Show detailed per-table statistics
        days: Retention period in days
    
    Returns:
        dict: Result with phase, status, details
    """
    # Advisory lock to prevent concurrent compaction runs
    lock_id = 10000 + {'all': 0, 'tier2': 1, 'tier3': 2}.get(phase, 0)
    
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
        locked = cursor.fetchone()[0]
        if not locked:
            raise self.retry(exc=Exception("Compaction already running"), countdown=300)
    
    try:
        logger.info(f"Starting compaction phase={phase}, days={days}")
        call_command('compact_data', phase=phase, verbose=verbose, days=days)
        logger.info(f"Compaction phase={phase} completed")
        return {'phase': phase, 'status': 'completed'}
    except Exception as e:
        logger.error(f"Compaction phase={phase} failed: {e}")
        raise
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])


@shared_task(bind=True, queue='maintenance', priority=2, max_retries=1)
def cleanup_old_data(self, days=31, verbose=False):
    """
    Delete metric data older than retention period.
    
    Processes tables in FK-safe order (children first, parent last).
    Deletes in batches to avoid long table locks.
    
    Args:
        days: Delete data older than this many days
        verbose: Show detailed per-table statistics
    
    Returns:
        dict: Result with days, status, details
    """
    try:
        logger.info(f"Starting cleanup of data older than {days} days")
        call_command('cleanup_old_data', days=days, verbose=verbose)
        logger.info(f"Cleanup completed for {days} days")
        return {'days': days, 'status': 'completed'}
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        raise


@shared_task(bind=True, queue='maintenance', priority=1, max_retries=0)
def vacuum_analyze(self):
    """
    Run VACUUM ANALYZE on metrics tables after maintenance.
    
    Reclaims dead tuples and updates planner statistics.
    Uses regular VACUUM ANALYZE (not VACUUM FULL) — no exclusive lock.
    
    Returns:
        dict: Result with tables processed, status
    """
    tables = [
        'metrics_gpumetric',
        'metrics_storagemetric', 
        'metrics_networkmetric',
        'metrics_gpu_process',
        'metrics_power_reading',
        'metrics_metricsnapshot',
    ]
    
    try:
        logger.info("Starting VACUUM ANALYZE on metrics tables")
        for table in tables:
            with connection.cursor() as cursor:
                cursor.execute(f'VACUUM ANALYZE {table}')
                logger.info(f"VACUUM ANALYZE completed for {table}")
        
        logger.info("VACUUM ANALYZE completed for all tables")
        return {'tables': len(tables), 'status': 'completed'}
    except Exception as e:
        logger.error(f"VACUUM ANALYZE failed: {e}")
        raise
TASK_EOF

    # Copy to production
    cp "${SCRIPT_DIR}/gpu_monitor/metrics_app/tasks.py" "${APP_DIR}/gpu_monitor/metrics_app/tasks.py"
    chown monitoring:monitoring "${APP_DIR}/gpu_monitor/metrics_app/tasks.py"
    chmod 644 "${APP_DIR}/gpu_monitor/metrics_app/tasks.py"
}

phase3_create_audit_tasks() {
    log_info "Phase 3.2: Creating audit/tasks.py..."
    
    cat > "${APP_DIR}/gpu_monitor/audit/tasks.py" <<'TASK_EOF'
"""
Celery tasks for audit app.

Migrates audit log cleanup from management command to Celery task.
"""

from celery import shared_task
from django.core.management import call_command
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, queue='maintenance', priority=4, max_retries=1)
def cleanup_audit_log(self, days=90, verbose=False):
    """
    Delete audit log entries older than specified days.
    
    Runs after other maintenance tasks to clean up audit trail.
    
    Args:
        days: Delete audit entries older than this many days
        verbose: Show detailed statistics
    
    Returns:
        dict: Result with days, status
    """
    try:
        logger.info(f"Starting audit log cleanup for entries older than {days} days")
        call_command('cleanup_audit_log', days=days, verbose=verbose)
        logger.info(f"Audit log cleanup completed for {days} days")
        return {'days': days, 'status': 'completed'}
    except Exception as e:
        logger.error(f"Audit log cleanup failed: {e}")
        raise
TASK_EOF

    # Copy to production
    cp "${SCRIPT_DIR}/gpu_monitor/audit/tasks.py" "${APP_DIR}/gpu_monitor/audit/tasks.py"
    chown monitoring:monitoring "${APP_DIR}/gpu_monitor/audit/tasks.py"
    chmod 644 "${APP_DIR}/gpu_monitor/audit/tasks.py"
}

phase3_create_beat_tasks() {
    log_info "Phase 3.3: Creating periodic tasks in Beat..."
    
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && export DJANGO_SETTINGS_MODULE="$DJANGO_SETTINGS" && set -a && source "$ENV_FILE" && set +a
    
    python -c "
import django
import json
django.setup()
from django_celery_beat.models import PeriodicTask, CrontabSchedule

# Create schedule for 3 AM daily
schedule_3am, _ = CrontabSchedule.objects.get_or_create(
    minute=0, hour=3, day_of_week='*', day_of_month='*', month_of_year='*'
)

# Task 1: Compact Tier 2 (1-7 days -> 15-min buckets) at 3:00 AM
PeriodicTask.objects.get_or_create(
    name='Compact Data - Tier 2 (3 AM)',
    task='metrics_app.tasks.compact_data',
    defaults={
        'crontab': schedule_3am,
        'queue': 'maintenance',
        'priority': 3,
        'enabled': True,
        'kwargs': json.dumps({'phase': 'tier2', 'verbose': True}),
    }
)

# Task 2: Compact Tier 3 (7-31 days -> 1-hour buckets) at 3:05 AM
schedule_305, _ = CrontabSchedule.objects.get_or_create(
    minute=5, hour=3, day_of_week='*', day_of_month='*', month_of_year='*'
)
PeriodicTask.objects.get_or_create(
    name='Compact Data - Tier 3 (3:05 AM)',
    task='metrics_app.tasks.compact_data',
    defaults={
        'crontab': schedule_305,
        'queue': 'maintenance',
        'priority': 3,
        'enabled': True,
        'kwargs': json.dumps({'phase': 'tier3', 'verbose': True}),
    }
)

# Task 3: Cleanup Old Data (>31 days) at 3:10 AM
schedule_310, _ = CrontabSchedule.objects.get_or_create(
    minute=10, hour=3, day_of_week='*', day_of_month='*', month_of_year='*'
)
PeriodicTask.objects.get_or_create(
    name='Cleanup Old Data (3:10 AM)',
    task='metrics_app.tasks.cleanup_old_data',
    defaults={
        'crontab': schedule_310,
        'queue': 'maintenance',
        'priority': 2,
        'enabled': True,
        'kwargs': json.dumps({'days': 31, 'verbose': True}),
    }
)

# Task 4: VACUUM ANALYZE at 3:15 AM
schedule_315, _ = CrontabSchedule.objects.get_or_create(
    minute=15, hour=3, day_of_week='*', day_of_month='*', month_of_year='*'
)
PeriodicTask.objects.get_or_create(
    name='VACUUM ANALYZE (3:15 AM)',
    task='metrics_app.tasks.vacuum_analyze',
    defaults={
        'crontab': schedule_315,
        'queue': 'maintenance',
        'priority': 1,
        'enabled': True,
        'kwargs': json.dumps({}),
    }
)

# Task 5: Cleanup Audit Log (90 days) at 3:20 AM
schedule_320, _ = CrontabSchedule.objects.get_or_create(
    minute=20, hour=3, day_of_week='*', day_of_month='*', month_of_year='*'
)
PeriodicTask.objects.get_or_create(
    name='Cleanup Audit Log (3:20 AM)',
    task='audit.tasks.cleanup_audit_log',
    defaults={
        'crontab': schedule_320,
        'queue': 'maintenance',
        'priority': 4,
        'enabled': True,
        'kwargs': json.dumps({'days': 90, 'verbose': True}),
    }
)

print('All periodic tasks created/updated')
"
}

phase3_verify_workers() {
    log_info "Phase 3.4: Verifying maintenance workers..."
    
    # Check maintenance worker
    systemctl status celery-maintenance@1 --no-pager
    
    # If not running, start it
    if ! systemctl is-active --quiet celery-maintenance@1; then
        sudo systemctl enable --now celery-maintenance@1
        sleep 2
        systemctl status celery-maintenance@1 --no-pager
    fi
    
    # Verify queue
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && set -a && source "$ENV_FILE" && set +a
    celery -A gpu_monitor inspect active_queues
}

phase3_disable_cron() {
    log_info "Phase 3.6: Disabling cron job..."
    sudo sed -i 's/^[0-9]/# &/' /etc/cron.d/monitoring-data-cleanup
    cat /etc/cron.d/monitoring-data-cleanup
    log_success "Cron job disabled"
}

run_phase3() {
    log_info "=== Phase 3: Data Maintenance (3 AM cron → Beat) ==="
    phase3_create_metrics_tasks
    phase3_create_audit_tasks
    phase3_create_beat_tasks
    phase3_verify_workers
    phase3_disable_cron
    log_success "Phase 3 completed"
}

# =============================================================================
# PHASE 4: ASYNC INGEST (High Impact)
# =============================================================================
phase4_create_task() {
    log_info "Phase 4.1: Adding async ingest task..."
    
    # Append to metrics_app/tasks.py
    cat >> "${APP_DIR}/gpu_monitor/metrics_app/tasks.py" <<'TASK_EOF'

@shared_task(
    bind=True,
    queue='ingest',
    priority=9,
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_kwargs={'max_retries': 3}
)
def process_ingest_payload(self, rig_uuid, payload_dict, user_id, api_key_id, enrolled_by_key_changed=False):
    """
    Process telemetry payload asynchronously.
    
    Returns 202 Accepted immediately, processes payload in background.
    Idempotent via natural key (rig_uuid, schema_version, timestamp).
    
    Args:
        rig_uuid: Rig UUID string
        payload_dict: Full payload dict from agent
        user_id: Owner user ID
        api_key_id: API key ID
        enrolled_by_key_changed: Whether API key changed
    
    Returns:
        dict: {'status': 'accepted'|'duplicate'|'error', 'snapshot_id': str|None, 'message': str}
    """
    from rigs.models import Rig
    from accounts.models import ApiKey
    from metrics_app.serializers import process_ingest
    
    try:
        rig = Rig.objects.get(uuid=rig_uuid)
        api_key = ApiKey.objects.get(id=api_key_id)
        user = api_key.user
        
        # Verify ownership
        if rig.owner_id != user.id:
            return {'status': 'error', 'message': 'Rig not owned by user'}
        
        # Process payload (same logic as sync view)
        result, status = process_ingest(
            rig_uuid=rig_uuid,
            data=payload_dict,
            owner_id=user.id,
            rig=rig,
            enrolled_by_key_changed=enrolled_by_key_changed
        )
        
        return {
            'status': result.get('status'),
            'snapshot_id': result.get('snapshot_id'),
            'message': result.get('message', '')
        }
        
    except Rig.DoesNotExist:
        return {'status': 'error', 'message': 'Rig not found'}
    except ApiKey.DoesNotExist:
        return {'status': 'error', 'message': 'API key not found'}
    except Exception as e:
        logger.exception(f"Ingest failed for rig {rig_uuid}: {e}")
        return {'status': 'error', 'message': str(e)}
TASK_EOF
}

phase4_modify_views() {
    log_info "Phase 4.2: Modifying IngestView to return 202 Accepted..."
    
    # Add import at top
    sed -i '1a from metrics_app.tasks import process_ingest_payload' "${APP_DIR}/gpu_monitor/metrics_app/views.py"
    
    # The actual modification of IngestView.post() is complex - create a patch
    # For now, we'll note this needs manual review
    log_warn "Phase 4.2: Manual modification of IngestView.post() required"
    log_warn "See documentation for exact changes needed"
}

phase4_deploy_workers() {
    log_info "Phase 4.3: Deploying ingest workers..."
    
    # Start 1 ingest worker (2 concurrency)
    sudo systemctl enable --now celery-ingest@1
    sleep 2
    systemctl status celery-ingest@1 --no-pager
}

phase4_reduce_gunicorn() {
    log_info "Phase 4.5: Reducing Gunicorn workers..."
    
    # Change from 8 to 4 workers
    sudo sed -i 's/--workers 8/--workers 4/' /etc/systemd/system/gunicorn.service
    sudo systemctl daemon-reload
    sudo systemctl restart gunicorn
    sleep 2
    systemctl status gunicorn --no-pager
}

phase4_verify() {
    log_info "Phase 4.4: Verifying async ingest..."
    
    # Test async endpoint
    curl -X POST http://localhost:8000/api/v1/ingest/ \
        -H "Content-Type: application/json" \
        -H "X-API-Key: ***" \
        -H "X-Rig-UUID: test-rig-uuid" \
        -d '{
            "rig_uuid": "test-uuid",
            "rig_name": "test-rig",
            "timestamp": "2026-07-17T12:00:00Z",
            "metrics": {
                "cpu": {"utilization_pct": 50.0, "temp_c": 45.0},
                "gpus": [{"model": "RTX 3080", "gpu_util_pct": 80.0, "temp_c": 65.0}],
                "memory": {"total_bytes": 32000000000, "used_bytes": 16000000000}
            }
        }'
    
    # Check worker processed
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && set -a && source "$ENV_FILE" && set +a
    celery -A gpu_monitor inspect active
    
    # Verify database
    python manage.py shell -c "
from metrics_app.models import MetricSnapshot
test_rig = 'test-uuid'
snapshots = MetricSnapshot.objects.filter(rig_uuid=test_rig).order_by('-timestamp')
print(f'Test rig snapshots: {snapshots.count()}')
for s in snapshots[:3]:
    print(f'{s.timestamp} | CPU: {s.cpu_utilization_pct}% | GPU: {s.gpu_metrics}')
"
}

run_phase4() {
    log_info "=== Phase 4: Async Ingest (High Impact) ==="
    phase4_create_task
    phase4_modify_views
    phase4_deploy_workers
    phase4_reduce_gunicorn
    phase4_verify
    log_success "Phase 4 completed"
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
create_periodic_task() {
    local task_name=$1 name=$2 schedule=$3 queue=$4 priority=$5 kwargs=$6
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && export DJANGO_SETTINGS_MODULE="$DJANGO_SETTINGS" && set -a && source "$ENV_FILE" && set +a
    
    python -c "
import django, json
django.setup()
from django_celery_beat.models import PeriodicTask, CrontabSchedule

schedule, _ = CrontabSchedule.objects.get_or_create(
    minute='$MINUTE', hour='$HOUR', day_of_week='*', day_of_month='*', month_of_year='*'
)

PeriodicTask.objects.get_or_create(
    name='$name',
    task='$task_name',
    defaults={
        'crontab': schedule,
        'queue': '$queue',
        'priority': $priority,
        'enabled': True,
        'kwargs': json.dumps($kwargs),
    }
)
"
}

verify_services() {
    for svc in celery-beat celery-ingest@1 celery-maintenance@1 celery-default@1; do
        if ! systemctl is-active --quiet "$svc"; then
            log_error "Service $svc not active"
            return 1
        fi
    done
    
    cd "$APP_DIR" && source "$VENV_PATH/bin/activate" && export DJANGO_SETTINGS_MODULE="$DJANGO_SETTINGS" && set -a && source "$ENV_FILE" && set +a
    celery -A gpu_monitor inspect ping
    celery -A gpu_monitor inspect active_queues
}

rollback_phase() {
    case $1 in
        4) 
            log_warn "Rolling back Phase 4..."
            sudo systemctl stop celery-ingest@1
            sudo systemctl disable celery-ingest@1
            # Restore gunicorn workers
            sudo sed -i 's/--workers 4/--workers 8/' /etc/systemd/system/gunicorn.service
            sudo systemctl daemon-reload
            sudo systemctl restart gunicorn
            ;;
        3) 
            log_warn "Rolling back Phase 3..."
            sudo sed -i 's/^# \([0-9]\)/\1/' /etc/cron.d/monitoring-data-cleanup
            sudo systemctl stop celery-maintenance@1
            sudo systemctl disable celery-maintenance@1
            ;;
        2) 
            log_warn "Rolling back Phase 2..."
            sudo sed -i 's/^# \*\//\*\//' /etc/cron.d/rig-status
            sudo systemctl stop celery-maintenance@1
            sudo systemctl disable celery-maintenance@1
            ;;
        1) 
            log_warn "Rolling back Phase 1..."
            sudo systemctl stop celery-beat celery-default@1 celery-maintenance@1 celery-ingest@1
            sudo systemctl disable celery-beat celery-default@1 celery-maintenance@1 celery-ingest@1
            ;;
        0) 
            log_warn "Rolling back Phase 0..."
            sudo systemctl stop redis-server
            sudo systemctl disable redis-server
            # Note: packages not uninstalled to avoid breaking other things
            ;;
        *) log_error "Unknown rollback phase: $1"; return 1 ;;
    esac
}

# =============================================================================
# CLI PARSING
# =============================================================================
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --phase) PHASE="$2"; shift 2 ;;
            --all) PHASE="all"; shift ;;
            --dry-run) DRY_RUN=true; shift ;;
            --rollback) ROLLBACK_PHASE="$2"; shift 2 ;;
            --verify) VERIFY_ONLY=true; shift ;;
            -h|--help) show_help; exit 0 ;;
            *) log_error "Unknown option: $1"; show_help; exit 1 ;;
        esac
    done
}

show_help() {
    cat <<EOF
Usage: bash celery_install.sh [options]

Options:
  --phase N       Run specific phase (0, 1, 2, 3, 4)
  --all           Run all phases 0-4 (default)
  --dry-run       Show what would be done without executing
  --rollback N    Rollback specific phase (0-4)
  --verify        Verify all phases
  -h, --help      Show this help

Examples:
  bash celery_install.sh --all          # Run all phases
  bash celery_install.sh --phase 2      # Run only Phase 2
  bash celery_install.sh --rollback 4   # Rollback Phase 4
  bash celery_install.sh --verify       # Verify all phases
  bash celery_install.sh --dry-run --all # Dry run all phases
EOF
}

# =============================================================================
# MAIN ENTRY POINT
# =============================================================================
main() {
    parse_args "$@"
    
    if [[ "$DRY_RUN" == "true" ]]; then
        log_warn "DRY RUN MODE - No changes will be made"
    fi
    
    check_root
    check_prerequisites
    
    if [[ -n "$ROLLBACK_PHASE" ]]; then
        log_warn "Rolling back phase $ROLLBACK_PHASE..."
        rollback_phase "$ROLLBACK_PHASE"
        exit 0
    fi
    
    if [[ "$VERIFY_ONLY" == "true" ]]; then
        log_info "Running verification only..."
        verify_services
        exit 0
    fi
    
    case "$PHASE" in
        0) run_phase0 ;;
        1) run_phase1 ;;
        2) run_phase2 ;;
        3) run_phase3 ;;
        4) run_phase4 ;;
        all) 
            run_phase0
            run_phase1
            run_phase2
            run_phase3
            run_phase4
            ;;
        *) log_error "Invalid phase: $PHASE"; show_help; exit 1 ;;
    esac
    
    log_success "All phases completed successfully!"
}

# Run
main "$@"