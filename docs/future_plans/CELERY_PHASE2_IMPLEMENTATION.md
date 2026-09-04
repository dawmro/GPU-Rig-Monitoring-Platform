# GPU Rig Monitoring Platform — Celery Phase 2 Implementation Guide

**Version:** 1.0  
**Date:** July 2026  
**Phase:** 2 — Migrate Rig Status Update (Low Risk, High Value)  
**Prerequisites:** Phase 0 (Redis + Celery infra) + Phase 1 (Celery app + workers + beat) complete  
**Risk:** Low — only migrates the 2-min rig status cron to Celery Beat + task  

---

## Phase 2 Overview

| Step | Description | Output |
|------|-------------|--------|
| 2.1 | Create `rigs/tasks.py` with `update_rig_status` task | Task module |
| 2.2 | Register task in Celery (autodiscover) | Auto-loaded |
| 2.3 | Create periodic task in Beat (every 2 min, queue `maintenance`) | Beat schedule entry |
| 2.4 | Start maintenance worker (if not running) | Worker running |
| 2.5 | Verify task runs correctly for 24-48h | Logs match cron |
| 2.6 | Disable cron job `/etc/cron.d/rig-status` | Cron disabled |

**Rollback:** Uncomment cron line, stop maintenance worker.

---

## 2.1 Create `rigs/tasks.py`

**File:** `/home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor/rigs/tasks.py`

```python
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
```

---

## 2.2 Verify Task Autodiscovery

The task will be auto-discovered because:
- `rigs` is in `INSTALLED_APPS`
- `app.autodiscover_tasks()` in `celery.py` scans all installed apps for `tasks.py`

**Verify:**
```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
celery -A gpu_monitor inspect registered | grep update_rig_status
```

Expected output shows `rigs.tasks.update_rig_status` in the registered tasks list.

---

## 2.3 Create Periodic Task in Beat

### Option A: Via Django Admin (Recommended)

1. Start Django admin:
   ```bash
   cd /opt/gpu_monitor
   source venv/bin/activate
   set -a && source .env && set +a
   python manage.py createsuperuser  # if not exists
   ```

2. Access admin at `https://your-domain.com/admin/`

3. Navigate to **Django Celery Beat** → **Periodic Tasks** → **Add Periodic Task**

3. Fill in:
   | Field | Value |
   |-------|-------|
   | **Name** | `Update Rig Status (every 2 min)` |
   | **Task** | `rigs.tasks.update_rig_status` |
   | **Queue** | `maintenance` |
   | **Priority** | `5` |
   | **Enabled** | ✓ |
   | **Interval** | Every 2 minutes (create IntervalSchedule: every=2, period=minutes) |

4. Save.

### Option B: Via Management Command (Alternative)

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python manage.py shell -c "
from django_celery_beat.models import PeriodicTask, IntervalSchedule
import json

schedule, _ = IntervalSchedule.objects.get_or_create(
    every=2,
    period=IntervalSchedule.MINUTES,
)

PeriodicTask.objects.get_or_create(
    name='Update Rig Status (every 2 min)',
    task='rigs.tasks.update_rig_status',
    defaults={
        'interval': schedule,
        'queue': 'maintenance',
        'priority': 5,
        'enabled': True,
    }
)
"
```

---

## 2.4 Start Maintenance Worker

The maintenance worker should already be running from Phase 1. Verify:

```bash
systemctl status celery-maintenance@1
```

If not running:
```bash
sudo systemctl enable --now celery-maintenance@1
sleep 2
systemctl status celery-maintenance@1
```

**Verify worker is listening on maintenance queue:**
```bash
celery -A gpu_monitor inspect active_queues
```

Should show `maintenance` queue on `maint-worker-1@...`.

---

## 2.5 Verify Task Execution (24-48h)

### Check Task Logs

```bash
# Watch beat logs for task scheduling
journalctl -u celery-beat -f | grep update_rig_status

# Watch maintenance worker logs
journalctl -u celery-maintenance@1 -f
```

### Check Database for Status Changes

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python manage.py shell -c "
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
```

### Compare with Cron Logs

```bash
# Old cron logs
cat /opt/gpu_monitor/logs/rig_status.log

# New Celery logs
journalctl -u celery-maintenance@1 --since "2 hours ago" | grep -E "(stale|offline|processed)"
```

### Expected Output Format

Celery task returns:
```json
{
  "stale": 2,
  "offline": 1,
  "processed": 3,
  "timestamp": "2026-07-16T10:46:00.123456+00:00"
}
```

Cron produced similar output in `/opt/gpu_monitor/logs/rig_status.log`:
```
Updated: 2 stale, 1 offline
```

---

## 2.6 Disable Cron Job

Once verified for 24-48 hours:

```bash
# Edit cron file
sudo nano /etc/cron.d/rig-status

# Comment out the line:
# */2 * * * * root bash /opt/gpu_monitor/deploy/update_rig_status.sh

# Or simply:
sudo sed -i 's/^\*/# *\//' /etc/cron.d/rig-status

# Verify disabled
cat /etc/cron.d/rig-status
```

**Keep the management command** for manual invocation:
```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python manage.py update_rig_status
```

---

## Verification Checklist

| Check | Pass Criteria |
|-------|---------------|
| Task registered | `celery inspect registered` shows `rigs.tasks.update_rig_status` |
| Periodic task created | Visible in Django admin → Periodic Tasks |
| Beat schedules it | `celery inspect scheduled` shows task every 2 min |
| Worker picks it up | Maintenance worker logs show task execution |
| Database updates | `RigStatusEvent` entries created every 2 min |
| Status transitions | Rigs transition ONLINE → STALE → OFFLINE correctly |
| No duplicate runs | Only one task execution per 2-min interval |
| Cron disabled | `/etc/cron.d/rig-status` line commented out |

---

## Rollback Procedure

If issues arise:

```bash
# 1. Re-enable cron
sudo sed -i 's/^# \*\//\*\//' /etc/cron.d/rig-status

# 2. Stop maintenance worker (optional)
sudo systemctl stop celery-maintenance@1

# 3. Remove periodic task (optional)
# Via Django admin: delete the periodic task
```

---

## Files Created/Modified

| File | Location | Purpose |
|------|----------|---------|
| `tasks.py` (new) | `gpu_monitor/rigs/tasks.py` | Celery task for rig status update |
| Periodic task | Django admin / DB | Beat schedule entry (every 2 min, queue `maintenance`) |

---

## Next Steps

Once Phase 2 verified for 24-48h:

1. **Phase 3** — Migrate Data Maintenance (compact_data, cleanup_old_data, vacuum_analyze, cleanup_audit_log)
2. **Phase 4** — Async Ingest (high impact, moves ingest off Gunicorn)

---

**Phase 2 Complete.** Rig status updates now run via Celery Beat + maintenance worker instead of cron.
