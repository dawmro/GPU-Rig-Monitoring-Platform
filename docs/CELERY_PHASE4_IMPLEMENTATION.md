# GPU Rig Monitoring Platform — Celery Phase 4 Implementation Guide

**Version:** 1.0  
**Date:** July 2026  
**Phase:** 4 — Async Telemetry Ingest (High Impact, Requires Care)  
**Prerequisites:** Phase 0 (Redis + Celery infra) + Phase 1 (Celery app + workers + beat) + Phase 2 (rig status) + Phase 3 (data maintenance) complete  
**Risk:** High — moves heavy payload processing off Gunicorn workers to dedicated Celery workers  

---

## Phase 4 Overview

| Step | Description | Output |
|------|-------------|--------|
| 4.1 | Create `process_ingest_payload` task in `metrics_app/tasks.py` | Async ingest task |
| 4.2 | Modify `IngestView.post()` to dispatch task, return 202 | Async view |
| 4.3 | Create `ingest` queue workers (3 instances) | Worker deployment |
| 4.4 | Load test & verify | Performance validation |
| 4.5 | Reduce Gunicorn workers | Resource optimization |

**Rollback:** Revert `IngestView.post()` to synchronous, stop ingest workers.

---

## 4.1 Create Async Ingest Task

**File:** `/home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor/metrics_app/tasks.py` (add to existing file)

```python
"""
Celery tasks for metrics_app app.

Async telemetry ingest and maintenance tasks.
"""

from celery import shared_task
from django.core.management import call_command
from django.db import connection
import logging

logger = logging.getLogger(__name__)

# ... existing maintenance tasks (compact_data, cleanup_old_data, etc.) ...


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
            data=payload_dict,           # Fixed: was payload_dict=payload_dict
            owner_id=user.id,            # Fixed: was user_id=user.id
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
```

---

## 4.2 Modify IngestView to Return 202 Accepted

**File:** `/home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor/metrics_app/views.py`

**Change `IngestView.post()` method:**

```python
# on the top add
from metrics_app.tasks import process_ingest_payload

# In IngestView.post() method - REPLACE the synchronous call with:

def post(self, request):
    user = request.user
    api_key = request.auth
    data = request.data

    if not isinstance(data, dict):
        return Response({'status': 'error', 'message': 'Expected JSON object'}, status=400)

    rig_uuid = str(data.get('rig_uuid', ''))
    if not rig_uuid:
        return Response({'status': 'error', 'message': 'Missing rig_uuid'}, status=400)

    # ── Timestamp sanity check (keep synchronous - fast) ────────────────
    ts = data.get('timestamp')
    if ts is not None:
        try:
            from datetime import datetime, timezone as dt_timezone
            from django.utils.dateparse import parse_datetime
            parsed = parse_datetime(str(ts))
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt_timezone.utc)
                now = datetime.now(dt_timezone.utc)
                diff = abs((parsed - now).total_seconds())
                if diff > self.MAX_PAST_S:
                    return Response(
                        {'status': 'error', 'message': f'Timestamp too old: {ts}'},
                        status=400,
                    )
                if parsed > now + __import__('datetime').timedelta(seconds=self.MAX_FUTURE_S):
                    return Response(
                        {'status': 'error', 'message': f'Timestamp too far in future: {ts}'},
                        status=400,
                    )
        except Exception:
            pass  # Let process_ingest handle it

    # Check ownership
    rig_name = data.get('rig_name', '').strip()
    try:
        rig = Rig.objects.get(uuid=rig_uuid)
    except Rig.DoesNotExist:
        name = rig_name or 'Unnamed Rig'
        rig = Rig.objects.create(
            uuid=rig_uuid,
            owner=user,
            name=name[:128],
            expected_gpus=0,
            enrolled_by_api_key=api_key,
        )
        log_audit_event(request, 'rig.enrolled', 'Rig', rig.uuid,
                      {'agent_version': data.get('agent_version', ''), 'ip': request.META.get('REMOTE_ADDR')})
    else:
        if rig.owner_id != user.id:
            return Response({'status': 'error', 'message': 'UUID already claimed by another user'}, status=409)

    # Update enrolled_by_api_key if changed
    enrolled_by_key_changed = rig.enrolled_by_api_key_id != api_key.id
    if enrolled_by_key_changed:
        rig.enrolled_by_api_key = api_key

    # Dispatch async task - return 202 immediately
    task = process_ingest_payload.delay(
        rig_uuid=rig_uuid,
        payload_dict=data,
        user_id=user.id,
        api_key_id=api_key.id,
        enrolled_by_key_changed=enrolled_by_key_changed
    )

    return Response(
        {'status': 'accepted', 'task_id': task.id, 'message': 'Payload accepted for processing'},
        status=202
    )
```

---

## 4.3 Copy Updated Files to Production

```bash
# Copy updated files to /opt
sudo cp /home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor/metrics_app/tasks.py /opt/gpu_monitor/metrics_app/
sudo cp /home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor/metrics_app/views.py /opt/gpu_monitor/metrics_app/

sudo chown monitoring:monitoring /opt/gpu_monitor/gpu_monitor/metrics_app/tasks.py /opt/gpu_monitor/metrics_app/views.py
sudo chmod 644 /opt/gpu_monitor/gpu_monitor/metrics_app/tasks.py /opt/gpu_monitor/metrics_app/views.py

# Verify
ls -la /opt/gpu_monitor/gpu_monitor/metrics_app/tasks.py /opt/gpu_monitor/metrics_app/views.py
```

---

## 4.4 Deploy Ingest Workers (1 instance)

### Systemd Units Already Exist (from Phase 1)

The ingest workers are defined as `celery-ingest@.service` template (1 instance).

```bash
# Start 1 ingest workers
for i in 1; do
    sudo systemctl enable --now celery-ingest@$i
    sleep 1
done

# Verify all running
for i in 1; do
    systemctl status celery-ingest@$i --no-pager
done
```

### Verify Workers Listening on Ingest Queue

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
celery -A gpu_monitor inspect active_queues
```

Expected output shows `ingest` queue on all 1 workers.

---

## 4.5 Load Test & Verify

### Test Async Ingest Endpoint

```bash
# Test async endpoint returns 202
curl -X POST https://your-domain.com/api/v1/ingest/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "X-Rig-UUID: test-rig-uuid" \
  -d '{
    "rig_uuid": "test-uuid",
    "rig_name": "test-rig",
    "timestamp": "2026-07-16T12:00:00Z",
    "metrics": {
        "cpu": {"utilization_pct": 50.0, "temp_c": 45.0},
        "gpus": [{"model": "RTX 3080", "gpu_util_pct": 80.0, "temp_c": 65.0}],
        "memory": {"total_bytes": 32000000000, "used_bytes": 16000000000}
    }
  }'
```
```bash
curl -X POST http://localhost:8000/api/v1/ingest/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: 7333b84fc485f3615043d2d502102fe4a26eb72bf270e3dbfc2a5ca750ec5e14" \
  -H "X-Rig-UUID: 22c0643e-6880-40ad-809e-f2c28582ca30" \
  -d '{
    "rig_uuid": "22c0643e-6880-40ad-809e-f2c28582ca30",
    "rig_name": "test-rig",
    "timestamp": "2026-07-17T10:04:00Z",
    "metrics": {
        "cpu": {"utilization_pct": 50.0, "temp_c": 45.0},
        "gpus": [{"model": "RTX 3080", "gpu_util_pct": 80.0, "temp_c": 65.0}],
        "memory": {"total_bytes": 32000000000, "used_bytes": 16000000000}
    }
  }'
```
**Expected response:** `202 Accepted` with `{"status": "accepted", "task_id": "..."}`

### Load Test (Simulate 1000 rigs/minute)

```bash
# Use hey or ab for load testing
hey -n 1000 -c 50 -H "Content-Type: application/json" -H "X-API-Key: YOUR_KEY" \
  -H "X-Rig-UUID: test-rig" -m POST \
  -d '{"rig_uuid":"test","rig_name":"test","timestamp":"2026-07-16T12:00:00Z","metrics":{...}}' \
  https://your-domain.com/api/v1/ingest/
```

### Monitor Metrics During Load

```bash
# Watch queue depth
watch -n 2 'celery -A gpu_monitor inspect active_queues'

# Watch worker CPU/memory
htop

# Check task processing rate
celery -A gpu_monitor inspect active
celery -A gpu_monitor inspect reserved
```

### Expected Results

| Metric | Before (Sync) | After (Async) |
|--------|---------------|---------------|
| Ingest response time | 200-800ms | **<50ms** (202 Accepted) |
| Gunicorn worker usage | High (blocked on ingest) | **Low** (free for dashboard) |
| 500 errors under load | Frequent | **None** |
| Dashboard responsiveness | Degraded under load | **Normal** |

---

## 4.5 Reduce Gunicorn Workers

After verifying async ingest works under load:

```bash
# Edit gunicorn service
sudo nano /etc/systemd/system/gunicorn.service

# Change --workers from 8 to 4 (or 2-3 if dashboard traffic is low)
# ExecStart=/opt/gpu_monitor/venv/bin/gunicorn \
#     gpu_monitor.wsgi:application \
#     --bind 127.0.0.1:8000 \
#     --workers 4 \
#     ...

sudo systemctl daemon-reload
sudo systemctl restart gunicorn
```

---

## Verification Checklist

| Check | Pass Criteria |
|-------|---------------|
| Task created | `celery inspect registered` shows `process_ingest_payload` |
| Workers running | 3 `celery-ingest@N` services active |
| Queue active | `celery inspect active_queues` shows `ingest` queue |
| Async endpoint | Returns `202 Accepted` with `task_id` |
| Task processes | `celery inspect active` shows task processing |
| Results stored | Database has new snapshots with correct data |
| No duplicate processing | `update_or_create` on natural key prevents duplicates |
| Gunicorn freed | Dashboard responsive under load |
| Reduced workers | Gunicorn runs with 4 workers (down from 8) |

---

### Rollback Procedure

```bash
# 1. Revert IngestView.post() to synchronous
# (restore original synchronous process_ingest call)

# 2. Stop ingest workers
sudo systemctl stop celery-ingest@1 celery-ingest@2 celery-ingest@3
sudo systemctl disable celery-ingest@1 celery-ingest@2 celery-ingest@3
```
# 3. Restore Gunicorn workers
sudo systemctl restart gunicorn
```

---

## Files Created/Modified

| File | Location | Purpose |
|------|----------|---------|
| `tasks.py` (updated) | `gpu_monitor/metrics_app/tasks.py` | Added `process_ingest_payload` async task |
| `views.py` (updated) | `gpu_monitor/metrics_app/views.py` | `IngestView.post()` returns 202 Accepted |
| Workers | systemd `celery-ingest@.service` | 1 ingest queue worker |

---

## Next Steps

After Phase 4 verified under load:

1. **Phase 5** — Future enhancements: alerts, reports, predictive analysis
2. **Monitor** — Set up Prometheus/Grafana for Celery metrics
3. **Scale** — Add more ingest workers as rig count grows

---

**Phase 4 Complete.** Telemetry ingest now runs asynchronously on dedicated workers, freeing Gunicorn for dashboard traffic.
