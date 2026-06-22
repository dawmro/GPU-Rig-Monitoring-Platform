# Log Analysis — Disk Space Risk Assessment

## Current Log Files in Production

### 1. Django App Log: `/opt/gpu_monitor/logs/app.log`
**Configuration:** `settings.py` LOGGING
```python
'file': {
    'class': 'logging.handlers.RotatingFileHandler',
    'filename': str(_log_file),  # /opt/gpu_monitor/logs/app.log
    'maxBytes': 10 * 1024 * 1024,  # 10 MB
    'backupCount': 3,  # app.log.1, app.log.2, app.log.3
}
```
**Max size:** 10 MB × 4 = **40 MB** ✅ Safe
**Rotation:** Built-in Python RotatingFileHandler

### 2. Gunicorn Access Log: `/opt/gpu_monitor/logs/gunicorn-access.log`
**Configuration:** `--access-logfile` in gunicorn.service
**Rotation:** ❌ NONE — grows indefinitely!
**Risk:** HIGH — Every HTTP request adds a line. At 16.67 RPS (1000 rigs/min) + dashboard traffic:
- ~200 bytes/line × 16.67 RPS × 86400 s/day = ~2.9 GB/day
- **This will consume disk space rapidly!**

### 3. Gunicorn Error Log: `/opt/gpu_monitor/logs/gunicorn-error.log`
**Configuration:** `--error-logfile` in gunicorn.service
**Rotation:** ❌ NONE — grows indefinitely!
**Risk:** LOW — Only errors are logged, typically < 1 MB/day

### 4. Cleanup Log: `/opt/gpu_monitor/logs/cleanup.log`
**Configuration:** Appended by data_retention.sh (daily)
**Rotation:** ❌ NONE — grows indefinitely!
**Risk:** LOW — Daily append, ~1 KB/day

### 5. Agent Log (per rig): `/var/log/monitoring-agent/agent.log`
**Configuration:** `agent/run.py` setup_logging
```python
handler = logging.handlers.RotatingFileHandler(
    log_dir / 'agent.log', maxBytes=10*1024*1024, backupCount=3
)
```
**Max size:** 10 MB × 4 = **40 MB per rig** ✅ Safe
**Rotation:** Built-in Python RotatingFileHandler

### 6. Agent Cron Log: `/var/log/monitoring-agent/cron.log`
**Configuration:** Cron output redirection
**Rotation:** ❌ NONE — grows indefinitely!
**Risk:** LOW — Only cron output, typically < 100 KB/day

## Summary Table

| Log File | Location | Rotation | Max Size | Risk |
|---|---|---|---|---|
| app.log | /opt/gpu_monitor/logs/ | ✅ RotatingFileHandler | 40 MB | ✅ Safe |
| gunicorn-access.log | /opt/gpu_monitor/logs/ | ❌ None | ∞ | 🔴 HIGH |
| gunicorn-error.log | /opt/gpu_monitor/logs/ | ❌ None | ∞ | 🟡 Low |
| cleanup.log | /opt/gpu_monitor/logs/ | ❌ None | ∞ | 🟡 Low |
| agent.log | /var/log/monitoring-agent/ | ✅ RotatingFileHandler | 40 MB/rig | ✅ Safe |
| cron.log | /var/log/monitoring-agent/ | ❌ None | ∞ | 🟡 Low |

## Critical Issue: Gunicorn Access Log

The gunicorn access log has NO rotation and will grow indefinitely. At production scale (1000 rigs), this could consume **~3 GB/day** or **~90 GB/month**.

## Recommended Fixes

### Fix 1: Add logrotate configuration for gunicorn logs

Create `/etc/logrotate.d/gpu-monitor`:
```
/opt/gpu_monitor/logs/gunicorn-*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 monitoring monitoring
    sharedscripts
    postrotate
        systemctl reload gunicorn
    endscript
}
```

### Fix 2: Add logrotate for cleanup.log

```
/opt/gpu_monitor/logs/cleanup.log {
    weekly
    missingok
    rotate 8
    compress
    delaycompress
    notifempty
    create 0640 monitoring monitoring
}
```

### Fix 3: Add logrotate for agent cron.log

```
/var/log/monitoring-agent/cron.log {
    weekly
    missingok
    rotate 4
    compress
    delaycompress
    notifempty
    create 0640 monitoring-agent monitoring-agent
}
```

### Fix 4: Reduce gunicorn access log verbosity (optional)

For production, consider using `--access-logfile -` (stdout) and letting systemd handle logging, or use a custom access log format that only logs errors and slow requests.

### Fix 5: Monitor disk space

Add a simple disk space check to the daily maintenance:
```bash
# In data_retention.sh
DISK_USAGE=$(df /opt | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_USAGE" -gt 80 ]; then
    echo "WARNING: Disk usage at ${DISK_USAGE}%"
fi
```

## Estimated Disk Usage (1000 rigs, 31-day retention)

| Component | Daily | Monthly |
|---|---|---|
| PostgreSQL (compacted) | 540 MB | 16.7 GB |
| Gunicorn access log (unrotated) | 2.9 GB | 87 GB 🔴 |
| Gunicorn access log (rotated, 14 days) | 2.9 GB | 40.5 GB |
| Agent logs (per rig) | 40 MB | 40 MB (rotated) |
| App log | 10 MB | 40 MB (rotated) |
| **Total (with rotation)** | **~3.5 GB** | **~57 GB** |
| **Total (without rotation)** | **~3.5 GB** | **~104 GB** |

**Conclusion:** Without log rotation, the gunicorn access log alone could consume 87 GB/month. With proper rotation (14-day retention), total disk usage stays manageable at ~57 GB/month for 1000 rigs.
