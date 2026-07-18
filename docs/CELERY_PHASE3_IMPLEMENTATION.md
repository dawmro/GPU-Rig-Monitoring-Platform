# GPU Rig Monitoring Platform — Celery Phase 3 Implementation Guide

**Version:** 1.0  
**Date:** July 2026  
**Phase:** 3 — Migrate Data Maintenance (Medium Risk)  
**Prerequisites:** Phase 0 (Redis + Celery infra) + Phase 1 (Celery app + workers + beat) + Phase 2 (rig status migration) complete  
**Risk:** Medium — migrates daily 3 AM maintenance cron to Celery Beat + maintenance workers  

---

## Phase 3 Overview

| Step | Description | Output |
|------|-------------|--------|
| 3.1 | Create `metrics_app/tasks.py` with maintenance tasks | Task module |
| 3.2 | Create `audit/tasks.py` with `cleanup_audit_log` task | Task module |
| 3.3 | Create periodic tasks in Beat (3 AM staggered) | Beat schedule entries |
| 3.4 | Verify maintenance workers running | Workers running |
| 3.5 | Monitor first 3-5 runs | Logs match cron |
| 3.6 | Disable cron job `/etc/cron.d/monitoring-data-cleanup` | Cron disabled |

**Rollback:** Re-enable cron, stop maintenance workers.

---

## 3.1 Create `metrics_app/tasks.py`

**File:** `/home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor/metrics_app/tasks.py`

```python
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
```

---

## 3.2 Create `audit/tasks.py`

**File:** `/home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor/audit/tasks.py`

```python
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
```

---

## 3.2 Copy Task Files to Production

```bash
# Copy tasks to /opt with correct permissions
sudo cp /home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor/metrics_app/tasks.py /opt/gpu_monitor/metrics_app/
sudo cp /home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor/audit/tasks.py /opt/gpu_monitor/audit/

sudo chown monitoring:monitoring /opt/gpu_monitor/metrics_app/tasks.py
sudo chown monitoring:monitoring /opt/gpu_monitor/audit/tasks.py
sudo chmod 644 /opt/gpu_monitor/metrics_app/tasks.py
sudo chmod 644 /opt/gpu_monitor/audit/tasks.py

# Verify
ls -la /opt/gpu_monitor/metrics_app/tasks.py /opt/gpu_monitor/audit/tasks.py
```

---

## 3.3 Create Periodic Tasks in Beat

Run the management command to create periodic tasks:

```bash
cd /opt/gpu_monitor
source venv/bin/activate
export DJANGO_SETTINGS_MODULE=gpu_monitor.settings
set -a && source .env && set +a

python -c "
import django
import json
django.setup()
from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule

# Create schedule for 3 AM daily
schedule_3am, _ = CrontabSchedule.objects.get_or_create(
    minute=0,
    hour=3,
    day_of_week='*',
    day_of_month='*',
    month_of_year='*',
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
    minute=5,
    hour=3,
    day_of_week='*',
    day_of_month='*',
    month_of_year='*',
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
    minute=10,
    hour=3,
    day_of_week='*',
    day_of_month='*',
    month_of_year='*',
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
    minute=15,
    hour=3,
    day_of_week='*',
    day_of_month='*',
    month_of_year='*',
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
    minute=20,
    hour=3,
    day_of_week='*',
    day_of_month='*',
    month_of_year='*',
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
```

Go to Django admin (admin/django_celery_beat/periodictask/), 
go to task and save it without making any changes to confirm syntax is correct.

---

## 3.4 Verify Maintenance Workers Running

```bash
# Check maintenance worker is running
systemctl status celery-maintenance@1 --no-pager

# If not running, start it
sudo systemctl enable --now celery-maintenance@1
sleep 2
systemctl status celery-maintenance@1 --no-pager

# Verify worker is listening on maintenance queue
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
celery -A gpu_monitor inspect active_queues
```

---

## 3.5 Monitor First 3-5 Runs (24-48h)

### Check Task Logs

```bash
# Watch beat logs for task scheduling
journalctl -u celery-beat -f | grep -E "(compact_data|cleanup_old_data|vacuum_analyze|cleanup_audit_log)"

# Watch maintenance worker logs
journalctl -u celery-maintenance@1 -f
```

### Check Database for Results

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a

python manage.py shell -c "
from django_celery_results.models import TaskResult
from django.utils import timezone
from datetime import timedelta

# Recent task results
recent = TaskResult.objects.filter(
    task_name__in=[
        'metrics_app.tasks.compact_data',
        'metrics_app.tasks.cleanup_old_data', 
        'metrics_app.tasks.vacuum_analyze',
        'audit.tasks.cleanup_audit_log'
    ],
    date_done__gte=timezone.now() - timedelta(hours=6)
).order_by('-date_done')

for t in recent:
    print(f'{t.date_done} | {t.task_name} | {t.status} | {t.result}')
"
```

### Compare with Cron Logs

```bash
# Old cron logs
cat /opt/gpu_monitor/logs/cleanup.log

# New Celery logs
journalctl -u celery-maintenance@1 --since "6 hours ago" | grep -E "(compact_data|cleanup_old_data|vacuum_analyze|cleanup_audit_log|status)"
```

---

## 3.6 Disable Cron Job

Once verified for 3-5 runs:

```bash
# Disable - comments out any line starting with a digit (cron time field)
sudo sed -i 's/^[0-9]/# &/' /etc/cron.d/monitoring-data-cleanup

# Verify disabled
cat /etc/cron.d/monitoring-data-cleanup

# Keep management commands for manual invocation
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python manage.py daily_maintenance --verbose
```

---

## Verification Checklist

| Check | Pass Criteria |
|-------|---------------|
| Tasks registered | `celery inspect registered` shows all 4 tasks |
| Periodic tasks created | Visible in Django admin → Periodic Tasks |
| Beat schedules them | `celery inspect scheduled` shows tasks at 3:00, 3:05, 3:10, 3:15, 3:20 AM |
| Workers pick them up | Maintenance worker logs show task execution |
| Database updates | Tables compacted, old data deleted, vacuum done |
| Audit log cleaned | `cleanup_audit_log` entries deleted |
| No duplicate runs | Only one task execution per scheduled time |
| Cron disabled | `/etc/cron.d/monitoring-data-cleanup` lines commented out |

---

## Rollback Procedure

```bash
# 1. Re-enable cron - uncomments the line
sudo sed -i 's/^# \([0-9]\)/\1/' /etc/cron.d/monitoring-data-cleanup

# 2. Stop maintenance workers (optional)
sudo systemctl stop celery-maintenance@1

# 3. Remove periodic tasks (optional)
# Via Django admin: delete the 5 periodic tasks
```

---

## Files Created/Modified

| File | Location | Purpose |
|------|----------|---------|
| `tasks.py` (new) | `gpu_monitor/metrics_app/tasks.py` | Maintenance tasks (compact, cleanup, vacuum) |
| `tasks.py` (new) | `gpu_monitor/audit/tasks.py` | Audit log cleanup task |
| Periodic tasks | Django admin / DB | Beat schedule entries (5 tasks at 3:00-3:20 AM) |

---

## Next Steps

Once Phase 3 verified for 3-5 runs:

1. **Phase 4** — Async Ingest (high impact, moves ingest off Gunicorn)

---

**Phase 3 Complete.** Data maintenance now runs via Celery Beat + maintenance workers instead of daily cron.
