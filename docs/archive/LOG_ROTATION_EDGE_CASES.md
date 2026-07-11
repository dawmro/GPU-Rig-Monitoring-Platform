# Log Rotation — Edge Case Analysis

## Current Config Coverage

### ✅ Covered by logrotate.conf (5 files):
1. `/opt/gpu_monitor/logs/gunicorn-access.log` — daily, 14 days
2. `/opt/gpu_monitor/logs/gunicorn-error.log` — weekly, 8 weeks
3. `/opt/gpu_monitor/logs/cleanup.log` — weekly, 8 weeks
4. `/opt/gpu_monitor/logs/rig_status.log` — weekly, 4 weeks
5. `/var/log/monitoring-agent/cron.log` — weekly, 4 weeks

### ✅ Covered by Python RotatingFileHandler (2 files):
6. `/opt/gpu_monitor/logs/app.log` — 10MB × 4 files (Django settings.py)
7. `/var/log/monitoring-agent/agent.log` — 10MB × 4 files (agent/run.py)

### ❌ NOT covered (2 files):
8. `/var/log/monitoring-agent/cleanup-cron.log` — from data_retention.sh cron
9. `/var/log/monitoring-agent/update.log` — from agent check_update.py

## Edge Cases Identified

### Edge Case 1: Missing cleanup-cron.log
**Risk:** LOW — Only 1 line currently, grows slowly with data_retention.sh
**Fix:** Add to logrotate.conf

### Edge Case 2: Missing update.log
**Risk:** LOW — Agent update check log, ~54 lines currently
**Fix:** Add to logrotate.conf

### Edge Case 3: Log file ownership mismatch
**Issue:** logrotate.conf creates files as `monitoring:monitoring` user, but:
- `gunicorn-access.log` and `gunicorn-error.log` are created by gunicorn (runs as `monitoring` user) ✅
- `cleanup.log` and `rig_status.log` are created by scripts running as `root` (via cron)
- `app.log` is created by Django/gunicorn (runs as `monitoring` user) ✅
- `agent/cron.log` is created by agent cron (runs as `monitoring-agent` user) ✅

**Problem:** `cleanup.log` and `rig_status.log` are owned by root, but logrotate tries to create them as `monitoring:monitoring`. This could cause permission errors.

**Fix:** Either:
a) Change logrotate config to use `root:root` for those files, OR
b) Change the cron jobs to run as `monitoring` user instead of root

### Edge Case 4: Log directory permissions
**Issue:** `/opt/gpu_monitor/logs/` needs to be writable by both `monitoring` (gunicorn) and `root` (cron scripts)

**Current state:** Directory is `drwxr-xr-x` owned by `qrv:qrv` — may cause issues

### Edge Case 5: `create` vs `copytruncate`
**Current config uses `create`** — this recreates the file with specified ownership. If the file doesn't exist, it's created. If it exists, it's moved and a new one is created.

**Potential issue:** If gunicorn has the file open, `create` will work because gunicorn's file descriptor still points to the old inode. The `postrotate` with `systemctl reload gunicorn` ensures gunicorn reopens the new file.

**This is correct behavior** ✅

### Edge Case 6: `sharedscripts` for gunicorn logs
**Current config uses `sharedscripts`** — the `postrotate` script runs once for all matching log files, not once per file. This is correct because we have two gunicorn log files and only want to reload gunicorn once.

**This is correct behavior** ✅

### Edge Case 7: `delaycompress`
**Current config uses `delaycompress`** — the most recent rotated file is not compressed. This is intentional so you can always read the latest rotated log without decompressing.

**This is correct behavior** ✅

### Edge Case 8: `notifempty`
**Current config uses `notifempty`** — rotation is skipped if the file is empty. This prevents creating empty compressed archives.

**This is correct behavior** ✅

### Edge Case 9: `missingok`
**Current config uses `missingok`** — logrotate won't error if a log file doesn't exist. This is important because not all log files may exist on every deployment.

**This is correct behavior** ✅

### Edge Case 10: Logrotate runs as root
**System behavior:** logrotate runs daily via `/etc/cron.daily/logrotate` as root. It reads all configs in `/etc/logrotate.d/`.

**No action needed** — this is standard Ubuntu behavior ✅

## Summary of Fixes Needed

| Issue | Severity | Fix |
|---|---|---|
| Missing `cleanup-cron.log` | LOW | Add to logrotate.conf |
| Missing `update.log` | LOW | Add to logrotate.conf |
| File ownership mismatch (cleanup.log, rig_status.log) | MEDIUM | Change cron to run as `monitoring` user OR change logrotate ownership |
| Directory permissions | MEDIUM | Ensure `/opt/gpu_monitor/logs/` is writable by both `monitoring` and `root` |

## Recommended logrotate.conf Update

Add the 2 missing files and fix ownership:

```bash
# Cleanup cron log — from data_retention.sh, low volume
/var/log/monitoring-agent/cleanup-cron.log {
    weekly
    missingok
    rotate 8
    compress
    delaycompress
    notifempty
    create 0640 root root
}

# Agent update check log — from check_update.py, low volume
/var/log/monitoring-agent/update.log {
    weekly
    missingok
    rotate 4
    compress
    delaycompress
    notifempty
    create 0640 monitoring-agent monitoring-agent
}

# Cleanup log — from data_retention.sh cron (runs as root)
/opt/gpu_monitor/logs/cleanup.log {
    weekly
    missingok
    rotate 8
    compress
    delaycompress
    notifempty
    create 0640 root root
}

# Rig status log — from update_rig_status.sh cron (runs as root)
/opt/gpu_monitor/logs/rig_status.log {
    weekly
    missingok
    rotate 4
    compress
    delaycompress
    notifempty
    create 0640 root root
}
```
