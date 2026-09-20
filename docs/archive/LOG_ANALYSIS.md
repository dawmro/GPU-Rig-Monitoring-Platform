# Log Analysis — Disk Space Risk Assessment

## Current Log Files in Production

### Server Logs (logrotate-managed)

| Log File | Location | Rotation | Retention | Mechanism |
|---|---|---|---|---|
| `gunicorn-access.log` | `/opt/gpu_monitor/logs/` | Daily | 14 days | logrotate |
| `gunicorn-error.log` | `/opt/gpu_monitor/logs/` | Weekly | 8 weeks | logrotate |
| `cleanup.log` | `/opt/gpu_monitor/logs/` | Weekly | 8 weeks | logrotate |
| `rig_status.log` | `/opt/gpu_monitor/logs/` | Weekly | 4 weeks | logrotate |
| `agent cron.log` | `/var/log/monitoring-agent/` | Weekly | 4 weeks | logrotate |
| `agent cleanup-cron.log` | `/var/log/monitoring-agent/` | Weekly | 8 weeks | logrotate |
| `agent update.log` | `/var/gpu_monitor/logs/` | Weekly | 4 weeks | logrotate |

### Application Logs (self-rotating)

| Log File | Location | Rotation | Max Size | Mechanism |
|---|---|---|---|---|
| `app.log` | `/opt/gpu_monitor/logs/` | 10 MB × 4 files | 40 MB | RotatingFileHandler |
| `agent.log` | `/var/log/monitoring-agent/` | 10 MB × 4 files | 40 MB/rig | RotatingFileHandler |

## Critical Issue: Gunicorn Access Log

The gunicorn access log has NO built-in rotation. At production scale (1000 rigs), this could consume **~3 GB/day** or **~90 GB/month**. The logrotate config at `/etc/logrotate.d/gpu-monitor` handles this with daily rotation and 14-day retention.

## Logrotate Configuration

Installed via: `sudo cp /opt/gpu_monitor/deploy/logrotate.conf /etc/logrotate.d/gpu-monitor`

Key features:
- `create` (not `copytruncate`) — recreates file with correct ownership
- `postrotate` with `systemctl reload gunicorn` — gunicorn reopens log files
- `sharedscripts` — postrotate runs once for all gunicorn logs
- `delaycompress` — latest rotated file stays uncompressed
- Correct ownership per file (monitoring:monitoring, root:root, monitoring-agent:monitoring-agent)

## Estimated Disk Usage (1000 rigs, 31-day retention)

| Component | Daily | Monthly |
|---|---|---|
| PostgreSQL (compacted) | 540 MB | 16.7 GB |
| Gunicorn access log (rotated, 14 days) | 2.9 GB | 40.5 GB |
| All other logs (rotated) | ~50 MB | ~2 GB |
| **Total** | **~3.5 GB** | **~59 GB** |

## Verification

```bash
# Check logrotate config is valid
sudo logrotate -d /etc/logrotate.d/gpu-monitor

# Force rotation (for testing)
sudo logrotate -f /etc/logrotate.d/gpu-monitor

# Check current log sizes
du -sh /opt/gpu_monitor/logs/
du -sh /var/log/monitoring-agent/
```
