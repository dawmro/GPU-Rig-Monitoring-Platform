# GPU Rig Monitoring Platform — Celery Phase 1 Implementation Guide

**Version:** 1.0  
**Date:** July 2026  
**Phase:** 1 — Infrastructure Only (No Task Migration)  
**Prerequisites:** Phase 0 complete (Redis running, Celery packages installed, `.env` configured, migrations run)  
**Risk:** Zero — no cron changes, no task migration, existing server untouched

---

## Phase 1 Overview

| Step | Description | Output |
|------|-------------|--------|
| 1.1 | Create `gpu_monitor/celery.py` | Celery app instance |
| 1.2 | Update `gpu_monitor/__init__.py` | Auto-import on Django startup |
| 1.3 | Create systemd unit files | 4 service files |
| 1.4 | Reload systemd & start services | Workers + Beat running |
| 1.5 | Verify deployment | `celery inspect ping` returns pong |

**No cron jobs removed.** Everything still runs via existing cron.

---

## 1.1 Create `gpu_monitor/celery.py`

**File:** `/opt/gpu_monitor/gpu_monitor/celery.py`

```python
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
```

**Verify syntax:**
```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python -c "from gpu_monitor.celery import app; print('Celery app:', app.main)"
```

---

## 1.2 Update `gpu_monitor/__init__.py`

**File:** `/opt/gpu_monitor/gpu_monitor/__init__.py`

```python
"""
Django project initialization.

Imports the Celery app to ensure it's loaded when Django starts.
This makes the `celery` command work and enables @shared_task decorators.
"""

from .celery import app as celery_app

__all__ = ('celery_app',)
```

**Verify:**
```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python -c "import gpu_monitor; print('Celery app loaded:', gpu_monitor.celery_app.main)"
```

---

## 1.3 Create Systemd Unit Files

Create **3 service files** in `/etc/systemd/system/` (1 ingest + 1 maintenance + 1 default + 1 beat = 4 total):

### A. Celery Ingest Worker (Template - **1 instance**)

**File:** `/etc/systemd/system/celery-ingest@.service`

```ini
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
```

### B. Celery Maintenance Worker (Template)

**File:** `/etc/systemd/system/celery-maintenance@.service`

```ini
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
```

### C. Celery Default Worker (Template)

**File:** `/etc/systemd/system/celery-default@.service`

```ini
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
```

### D. Celery Beat Scheduler (Single Instance)

**File:** `/etc/systemd/system/celery-beat.service`

```ini
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
```

---

## 1.4 Reload systemd & Start Services

### Step 1: Reload systemd daemon

```bash
sudo systemctl daemon-reload
```

### Step 2: Verify unit files are valid

```bash
# Check syntax
systemd-analyze verify /etc/systemd/system/celery-ingest@.service
systemd-analyze verify /etc/systemd/system/celery-maintenance@.service
systemd-analyze verify /etc/systemd/system/celery-default@.service
systemd-analyze verify /etc/systemd/system/celery-beat.service
```

### Step 3: Create runtime directories

```bash
# For beat pidfile/schedule
sudo mkdir -p /var/run/celery /var/lib/celery
sudo chown monitoring:monitoring /var/run/celery /var/lib/celery
```

### Step 4: Start Beat first (scheduler must be running before workers)

```bash
sudo systemctl enable --now celery-beat
sleep 3
sudo systemctl status celery-beat
```

**Expected:** Active (running), no errors in journal.

### Step 5: Start ONE default worker (Phase 1 only needs 1)

```bash
sudo systemctl enable --now celery-default@1
sleep 2
sudo systemctl status celery-default@1
```

### Step 6: (Optional for Phase 1) Start maintenance worker

```bash
sudo systemctl enable --now celery-maintenance@1
sleep 2
sudo systemctl status celery-maintenance@1
```

### Step 7: (Phase 4 will scale these) Start ingest workers — SKIP for Phase 1

```bash
# DO NOT RUN IN PHASE 1 — Phase 4 will enable these
# for i in 1 2 3 4; do sudo systemctl enable --now celery-ingest@$i; done
```

---

## 1.5 Verify Deployment

### A. Check all services are running

```bash
systemctl status celery-beat celery-default@1 celery-maintenance@1
```

### B. Test Celery connectivity (the critical validation)

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a

# This should return 'pong' from each worker
celery -A gpu_monitor inspect ping
```

**Expected output:**
```
->  ingest-worker-1@hostname: OK
    * queues: ingest (2 concurrency)
->  maint-worker-1@hostname: OK
    * queues: maintenance (1 concurrency)
->  default-worker-1@hostname: OK
    * queues: default, alerts, reports (1 concurrency)
```

### C. Verify Beat scheduler is creating entries

```bash
# Check that django_celery_beat tables are being used
celery -A gpu_monitor inspect scheduled
```

**Expected:** Shows periodic tasks (empty initially, but command succeeds).

### D. Check logs for errors

```bash
# Beat logs
journalctl -u celery-beat -n 50 --no-pager

# Worker logs
journalctl -u celery-default@1 -n 50 --no-pager
journalctl -u celery-maintenance@1 -n 50 --no-pager
```

### E. Verify existing server still works

```bash
# Dashboard health
curl -s https://your-domain.com/api/v1/health/ | python3 -m json.tool

# Gunicorn status
systemctl status gunicorn
```

---

## Verification Checklist

| Check | Command | Expected |
|-------|---------|----------|
| Beat running | `systemctl is-active celery-beat` | `active` |
| Default worker running | `systemctl is-active celery-default@1` | `active` |
| Maintenance worker running | `systemctl is-active celery-maintenance@1` | `active` |
| Celery ping | `celery -A gpu_monitor inspect ping` | `pong` from all workers |
| Scheduled tasks | `celery -A gpu_monitor inspect scheduled` | No errors |
| Dashboard health | `curl /api/v1/health/` | `{"status": "healthy", ...}` |
| Gunicorn unaffected | `systemctl is-active gunicorn` | `active` |

---

## Rollback (If Needed)

```bash
# Stop Celery services
sudo systemctl stop celery-beat celery-default@1 celery-maintenance@1
sudo systemctl disable celery-beat celery-default@1 celery-maintenance@1

# Verify cron still works
systemctl status gunicorn
curl -s https://your-domain.com/api/v1/health/ | python3 -m json.tool
```

---

## Next Steps

Once all verification checks pass:

1. **Monitor for 1–2 hours** — watch logs for any errors
2. **Proceed to Phase 2** — Create `rigs/tasks.py` with `update_rig_status` task
3. **Add periodic task in Beat** — via Django admin or management command
4. **Start maintenance worker** (already done) and disable rig-status cron after verification

---

## Reference: Systemd Unit Parameters Explained

| Parameter | Ingest Worker | Maintenance Worker | Default Worker | Beat |
|-----------|---------------|-------------------|----------------|------|
| `--queues` | `ingest` | `maintenance` | `default,alerts,reports` | N/A |
| `--concurrency` | 2 | 1 | 1 | N/A |
| `--max-tasks-per-child` | 100 | 10 | 50 | N/A |
| `--time-limit` | 300s | 7200s | 300s | N/A |
| `--soft-time-limit` | 240s | 6600s | 240s | N/A |
| `--hostname` | `ingest-worker-%i@%h` | `maint-worker-%i@%h` | `default-worker-%i@%h` | N/A |
| `--pool` | prefork | prefork | prefork | N/A |

**Rationale:**
- **Ingest workers**: Medium concurrency (2), short timeouts, recycle often (100 tasks) for memory safety
- **Maintenance workers**: Low concurrency (1), very long timeouts (2h) for compaction/VACUUM, recycle rarely
- **Default workers**: Low concurrency (1), handles alerts/reports
- **Beat**: Single instance, DatabaseScheduler persists schedule in PostgreSQL

---

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| `celery inspect ping` times out | Workers not started or wrong broker URL | Check `systemctl status`, verify `.env` has correct `CELERY_BROKER_URL` |
| Workers show `Connection refused` | Redis not running or wrong password | `systemctl status redis-server`, check `.env` password |
| Beat fails with `django_celery_beat` error | Migrations not run | Run Phase 0 Step 3 migrations |
| `ModuleNotFoundError: gpu_monitor.celery` | `__init__.py` missing import | Verify `gpu_monitor/__init__.py` has `from .celery import app as celery_app` |
| Permission denied on pidfile | RuntimeDirectory not created | `sudo mkdir -p /var/run/celery && sudo chown monitoring:monitoring /var/run/celery` |

---

**Phase 1 Complete.** Proceed to Phase 2 when all verification checks pass.