# GPU Rig Monitoring Platform — Celery Phase 0 Implementation Guide

**Version:** 1.0  
**Date:** July 2026  
**Phase:** 0 — Prerequisites (Infrastructure Only)  
**Risk:** Zero — no code changes, no cron modifications, no Django config changes, existing server untouched  
**Prerequisites:** Ubuntu 22.04/24.04 server with root/sudo access, Django project at `/opt/gpu_monitor`

---

## Phase 0 Overview

| Step | Description | Verification |
|------|-------------|--------------|
| 0.1 | Install & configure Redis server | `redis-cli -a PASS ping` → `PONG` |
| 0.2 | Install Celery Python packages | Imports work without errors |
| 0.3 | Add Redis/Celery config to `.env` | Environment loads correctly |
| 0.4 | Configure Django settings (`settings.py`) | `CELERY_BROKER_URL` resolves, test ping succeeds |
| 0.5 | Add `django_celery_beat` & `django_celery_results` to `INSTALLED_APPS` | Apps recognized by Django |
| 0.6 | Run migrations for Celery apps | Tables created in PostgreSQL |
| 0.7 | Verify existing server still works | Dashboard health, Gunicorn active |

**No cron jobs removed. No code changes. No Django views modified.**

---

## 0.1 Install & Configure Redis Server

### On the production server (as root/sudo):

```bash
# 1. Install Redis
sudo apt update
sudo apt install -y redis-server
```

### 2. Configure Redis (secure, memory-bound, no persistence for broker)

```bash
sudo nano /etc/redis/redis.conf
```

**Change these lines in `/etc/redis/redis.conf`:**

```conf
# Bind to localhost only (no external access)
bind 127.0.0.1 -::1

# Set a strong password (generate one first):
# python3 -c "import secrets; print(secrets.token_urlsafe(32))"
requirepass YOUR_GENERATED_PASSWORD_HERE

# Limit memory, evict LRU when full (broker data is disposable)
maxmemory 2gb
maxmemory-policy allkeys-lru

# Disable RDB persistence (broker data is disposable)
save ""

# Keep AOF for durability if you want, or disable:
appendonly no
```

### 3. Restart Redis

```bash
sudo systemctl restart redis-server
sudo systemctl enable redis-server
```

### 4. Verify Redis works

```bash
redis-cli -a YOUR_PASSWORD ping
# Should return: PONG
```

---

## 0.2 Install Celery Python Packages

### In the Django virtualenv (as `monitoring` user):

```bash
cd /opt/gpu_monitor
source venv/bin/activate

# Install Celery stack
pip install celery redis django-celery-beat django-celery-results

# Verify imports work
python -c "
import celery, redis, django_celery_beat, django_celery_results
print('celery:', celery.__version__)
print('redis:', redis.__version__)
print('django-celery-beat:', django_celery_beat.__version__)
print('django-celery-results:', django_celery_results.__version__)
"
```

**Expected output (versions may vary):**
```
celery: 5.4.0
redis: 5.2.0
django-celery-beat: 2.5.0
django-celery-results: 2.5.1
```

---

## 0.3 Add Redis/Celery Config to `.env`

### Generate a strong Redis password (if not already done):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Example output: kRiAekuM6xTmWdONAc2JYNQtc1iUDUZfpe5sV-Lbf7c
```

### Add to `/opt/gpu_monitor/.env` (don't commit this file):

```bash
# Generate password if you haven't:
# python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Redis (components — same pattern as PostgreSQL)
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=kRiAekuM6xTmWdONAc2JYNQtc1iUDUZfpe5sV-Lbf7c
REDIS_DB_BROKER=0
REDIS_DB_RESULTS=1
```

### Reload the env for current shell:

```bash
set -a && source .env && set +a
```

---

## 0.4 Configure Django Settings (`settings.py`)

### Add near your `DATABASES` config in `/opt/gpu_monitor/gpu_monitor/settings.py`:

```python
# Redis / Celery — build URLs from components (same pattern as DB)
REDIS_HOST = os.environ.get('REDIS_HOST', '127.0.0.1')
REDIS_PORT = os.environ.get('REDIS_PORT', '6379')
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', '')
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
```

### Test settings load correctly:

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a

python -c "
import os, redis, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gpu_monitor.settings')
django.setup()

from django.conf import settings
print('Broker:', settings.CELERY_BROKER_URL)
print('Backend:', settings.CELERY_RESULT_BACKEND)

# Test actual connection
r = redis.from_url(settings.CELERY_BROKER_URL)
print('Broker ping:', r.ping())
r2 = redis.from_url(settings.CELERY_RESULT_BACKEND)
print('Backend ping:', r2.ping())
"
```

**Expected output:**
```
Broker: redis://:YOUR_PASSWORD@127.0.0.1:6379/0
Backend: redis://:YOUR_PASSWORD@127.0.0.1:6379/1
Broker ping: True
Backend ping: True
```

---

## 0.5 Add Celery Apps to `INSTALLED_APPS`

### In `/opt/gpu_monitor/gpu_monitor/settings.py`, add to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ... existing apps ...
    'django_celery_beat',
    'django_celery_results',
]
```

### Verify apps are recognized:

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python manage.py check
# Should pass without errors about missing apps
```

---

## 0.6 Run Migrations for Celery Apps

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a

# Creates: django_celery_beat_* and django_celery_results_* tables
python manage.py migrate django_celery_beat
python manage.py migrate django_celery_results

# Verify tables exist
echo "\dt django_celery_*" | python manage.py dbshell
# or directly with psql (source .env first):
# set -a && source .env && set +a && PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -c "\dt django_celery_*"
```

**Expected tables created:**
```
django_celery_beat_clockedschedule
django_celery_beat_crontabschedule
django_celery_beat_intervalschedule
django_celery_beat_periodictask
django_celery_beat_periodictasks
django_celery_beat_solarschedule
django_celery_results_taskresult
django_celery_results_groupresult
```

---

## 0.7 Verify Existing Server Still Works

### Dashboard health endpoint:

```bash
curl -s https://your-domain.com/api/v1/health/ | python3 -m json.tool
```

**Expected:**
```json
{
    "status": "healthy",
    "version": "1.0.0",
    "uptime_s": 12345,
    "db_connection": "ok",
    "active_rigs": 0
}
```

### Gunicorn status:

```bash
systemctl status gunicorn
```

**Expected:** `Active: active (running)` with no errors.

### Quick regression check:

```bash
# Verify cron jobs still exist
ls -la /etc/cron.d/rig-status /etc/cron.d/monitoring-data-cleanup /etc/cron.d/gpu-monitor-backup

# Verify logs are being written
tail -5 /opt/gpu_monitor/logs/gunicorn-access.log
tail -5 /opt/gpu_monitor/logs/cleanup.log
```

---

## Verification Checklist

| Check | Command | Expected Result |
|-------|---------|-----------------|
| Redis running | `systemctl is-active redis-server` | `active` |
| Redis ping | `redis-cli -a PASS ping` | `PONG` |
| Redis config | `redis-cli -a PASS config get maxmemory` | `2147483648` (2GB) |
| Celery packages | `pip list | grep -E "celery|redis|django-celery"` | All 4 packages listed |
| `.env` has Redis config | `grep REDIS /opt/gpu_monitor/.env` | 5 REDIS_* lines |
| Settings load | `python -c "import django; django.setup(); from django.conf import settings; print(settings.CELERY_BROKER_URL)"` | Redis URL with password |
| Redis ping from Django | `python -c "import redis; r=redis.from_url('redis://:PASS@...'); print(r.ping())"` | `True` |
| INSTALLED_APPS | `python manage.py check` | `System check identified no issues` |
| Migrations applied | `echo "\dt django_celery_*" \| python manage.py dbshell` | 8 tables listed |
| Dashboard health | `curl -s /api/v1/health/` | `{"status": "healthy"}` |
| Gunicorn active | `systemctl is-active gunicorn` | `active` |

---

## What Changed / What Didn't

| ✅ Changed | ❌ Didn't Change |
|-----------|------------------|
| Redis installed & secured | Zero Django code modified |
| Celery packages in venv | Zero cron jobs modified |
| `.env` has Redis config | Zero systemd services for Celery yet |
| Settings updated with Celery config | Zero tasks created |
| INSTALLED_APPS updated | Zero Django views modified |
| Celery migrations applied | Zero worker processes running |
| Connectivity verified | Existing server fully functional |

---

## Rollback (If Needed)

```bash
# Stop Redis
sudo systemctl stop redis-server
sudo systemctl disable redis-server

# Remove packages
cd /opt/gpu_monitor
source venv/bin/activate
pip uninstall celery redis django-celery-beat django-celery-results

# Remove from settings.py
# - Remove REDIS_* variables
# - Remove _redis_url function
# - Remove CELERY_* settings
# - Remove 'django_celery_beat', 'django_celery_results' from INSTALLED_APPS

# Revert migrations (if needed)
python manage.py migrate django_celery_beat zero
python manage.py migrate django_celery_results zero

# Remove .env Redis entries
# Verify server still works
systemctl status gunicorn
curl -s /api/v1/health/
```

---

## Next Steps

Once all verification checks pass:

1. **Proceed to Phase 1** → Create `gpu_monitor/celery.py`, `__init__.py`, systemd units, start workers + beat
2. **No cron changes yet** — everything still runs via existing cron
3. **Phase 2** will migrate the first task (`update_rig_status`)

---

## Reference: Redis Config Explanation

| Setting | Value | Reason |
|---------|-------|--------|
| `bind 127.0.0.1 ::1` | Localhost only | No external access; security |
| `requirepass` | Strong password | Defense in depth; local process compromise mitigation |
| `maxmemory 2gb` | 2 GB limit | Prevents OOM; broker data is disposable |
| `maxmemory-policy allkeys-lru` | LRU eviction | Standard for cache/broker workloads |
| `save ""` | Disable RDB | Broker data doesn't need persistence |
| `appendonly no` | Disable AOF | Optional; disable for pure broker use |

---

## Reference: Celery Settings Explanation

| Setting | Value | Reason |
|---------|-------|--------|
| `CELERY_TASK_TRACK_STARTED` | `True` | Track task start time in results |
| `CELERY_TASK_TIME_LIMIT` | `300` (5 min) | Hard timeout; kill stuck tasks |
| `CELERY_TASK_SOFT_TIME_LIMIT` | `240` (4 min) | Graceful timeout; allows cleanup |
| `CELERY_WORKER_PREFETCH_MULTIPLIER` | `1` | Fair dispatch; one task per worker at a time |
| `CELERY_WORKER_MAX_TASKS_PER_CHILD` | `100` | Recycle workers to prevent memory leaks |
| `CELERY_RESULT_EXPIRES` | `86400` (24h) | Auto-cleanup old results |
| `CELERY_TASK_VISIBILITY_TIMEOUT` | `3600` (1h) | Covers long compaction tasks |
| `CELERY_ACCEPT_CONTENT` | `['json']` | Security: only accept JSON |
| `CELERY_TASK_SERIALIZER` | `'json'` | Interoperability; no pickle |
| `CELERY_RESULT_SERIALIZER` | `'json'` | Same as above |
| `CELERY_TIMEZONE` | `'UTC'` | Consistent timestamps |
| `CELERY_BEAT_SCHEDULER` | `DatabaseScheduler` | Persists schedule in PostgreSQL; survives restarts; admin UI |

---

**Phase 0 Complete.** Proceed to **Phase 1** (create Celery app, systemd units, start workers + beat).
