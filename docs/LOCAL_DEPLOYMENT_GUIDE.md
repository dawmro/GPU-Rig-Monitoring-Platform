# GPU Rig Monitoring Platform — Local Deployment Guide

**Version:** 1.2
**Target OS:** Ubuntu 24.04 LTS (local testing, no domain name)

This guide walks through deploying the complete GPU Rig Monitoring Platform on a **local Ubuntu machine** for development, testing, or evaluation before deploying to production hardware.

By the end you will have:

- A **Django dashboard** accessible at `http://localhost/` showing real-time rig telemetry
- A **monitoring agent** running via cron that collects hardware metrics and sends them to the server
- Live updating metrics with HTMX-powered 30-second polling

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Server Setup](#3-server-setup)
   - 3.1 [Create the Installation Directory](#31-create-the-installation-directory)
   - 3.2 [Install System Packages](#32-install-system-packages)
   - 3.3 [Configure PostgreSQL](#33-configure-postgresql)
   - 3.4 [Set Up the Django Project](#34-set-up-the-django-project)
   - 3.5 [Run Migrations](#35-run-migrations)
   - 3.6 [Create an Admin User](#36-create-an-admin-user)
   - 3.7 [Configure Nginx](#37-configure-nginx)
   - 3.8 [Start Gunicorn](#38-start-gunicorn)
   - 3.9 [Quick Alternative: Django Dev Server](#39-quick-alternative-django-dev-server)
4. [Agent Setup](#4-agent-setup)
   - 4.1 [Create Agent User and Directories](#41-create-agent-user-and-directories)
   - 4.2 [Install Agent Dependencies](#42-install-agent-dependencies)
   - 4.3 [Copy Agent Files](#43-copy-agent-files)
   - 4.4 [Configure the Agent](#44-configure-the-agent)
   - 4.5 [Set Up Sudoers](#45-set-up-sudoers)
   - 4.6 [Set Up Cron](#46-set-up-cron)
   - 4.7 [Test the Agent](#47-test-the-agent)
5. [First Run Verification](#5-first-run-verification)
   - 5.1 [Check Server Health](#51-check-server-health)
   - 5.2 [Log Into the Dashboard](#52-log-into-the-dashboard)
   - 5.3 [Create an API Key](#53-create-an-api-key)
   - 5.4 [Verify the Agent Appears on the Dashboard](#54-verify-the-agent-appears-on-the-dashboard)
   - 5.5 [Verify HTMX Live Polling](#55-verify-htmx-live-polling)
   - 5.6 [Dashboard Features](#56-dashboard-features)
   - 5.7 [Set Up Data Retention](#57-set-up-data-retention)
   - 5.8 [Set Up Rig Status Monitoring Cron](#58-set-up-rig-status-monitoring-cron)
6. [Understanding the File Layout](#6-understanding-the-file-layout)
7. [Troubleshooting](#7-troubleshooting)
8. [Differences from Production](#8-differences-from-production)
9. [Preparing for Production Deployment](#9-preparing-for-production-deployment)
10. [Stopping, Restarting, and Upgrading](#10-stopping-restarting-and-upgrading)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  LOCAL MACHINE                       │
│                                                      │
│  ┌──────────────┐   HTTP POST   ┌────────────────┐  │
│  │ Agent        │ ──────────→   │ Nginx :80       │  │
│  │ (cron 60s)  │               │                 │  │
│  └──────────────┘               └───────┬────────┘  │
│                                         │ proxy      │
│                                         ▼            │
│                                 ┌────────────────┐  │
│                                 │ Gunicorn :8000  │  │
│                                 │ (Django + DRF) │  │
│                                 └───────┬────────┘  │
│                                         │            │
│                                         ▼            │
│                                 ┌────────────────┐  │
│                                 │ PostgreSQL     │  │
│                                 │ :5432          │  │
│                                 └────────────────┘  │
│                                                      │
│  Browser ──http://localhost──→ Nginx ──→ Django      │
│  (HTMX dashboard polling every 30s)                  │
└─────────────────────────────────────────────────────┘
```

**Key components:**

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Server | Django 6.x + DRF | API, dashboard, auth |
| Database | PostgreSQL 16 | Relational + metric storage |
| Web server | Nginx | Reverse proxy, static files |
| App server | Gunicorn | WSGI HTTP server |
| Dashboard | Django Templates + HTMX | Server-rendered UI with live polling |
| Agent | Python 3 + psutil/pynvml | Hardware metric collection |
| Scheduler | cron | Runs agent every 60 seconds |

---

## 2. Prerequisites

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| **OS** | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| **vCPU** | 2 | 4 |
| **RAM** | 4 GB | 8 GB |
| **Storage** | 20 GB | 50 GB |
| **Python** | 3.10+ | 3.12 (ships with Ubuntu 24.04) |
| **Access** | `sudo` privileges | — |

Verify your system:

```bash
python3 --version    # 3.10+
lsb_release -a       # Ubuntu 22.04 or 24.04
```

### Project Files

This guide assumes the project is checked out at:

```
/home/qrv/workspace/GPU-Rig-Monitoring-Platform/
```

If your path is different, adjust all `cp` commands accordingly.

---

## 3. Server Setup

### 3.1 Create the Installation Directory

```bash
sudo mkdir -p /opt/gpu_monitor
sudo chown "$USER:$USER" /opt/gpu_monitor
```

### 3.2 Install System Packages

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip postgresql postgresql-contrib \
    nginx curl build-essential
```

### 3.3 Configure PostgreSQL

```bash
# Start and enable PostgreSQL
sudo systemctl enable postgresql
sudo systemctl start postgresql

# Create database user and database
sudo -u postgres psql << 'EOF'
CREATE USER gpu_monitor WITH PASSWORD 'local_dev_password';
CREATE DATABASE gpu_monitor OWNER gpu_monitor;
GRANT ALL PRIVILEGES ON DATABASE gpu_monitor TO gpu_monitor;
EOF
```

> **Note:** For local testing we use plain PostgreSQL. All metric tables work as regular PostgreSQL tables.

**Verify the connection:**

```bash
PGPASSWORD=local_...word psql -h 127.0.0.1 -U gpu_monitor -d gpu_monitor -c "SELECT 1;"
```

If you see `?column? | 1`, the database is ready.

### 3.4 Set Up the Django Project

```bash
# Copy the Django project (adjust path if your checkout is elsewhere)
cp -r /home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor/* /opt/gpu_monitor/

# Create directories Django needs
mkdir -p /opt/gpu_monitor/logs
mkdir -p /opt/gpu_monitor/staticfiles

# Fix permissions — Gunicorn needs to read all files
# If you later add new template/views files, re-run this:
chmod -R 644 /opt/gpu_monitor/templates/
chmod -R 755 /opt/gpu_monitor/templates/dashboard/

# Create and activate virtualenv
cd /opt/gpu_monitor
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install django djangorestframework django-htmx psycopg2-binary \
    argon2-cffi gunicorn requests pyyaml psutil
```

> **Tip:** There is no `requirements.txt` in the repository. The packages listed above cover all Django server dependencies.

**Create the environment file** at `/opt/gpu_monitor/.env`:

```bash
cat > /opt/gpu_monitor/.env << 'EOF'
DJANGO_SECRET_KEY=change-me-generate-a-random-value-with-python-secrets
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=*
DB_NAME=gpu_monitor
DB_USER=gpu_monitor
DB_PASSWORD=local_dev_password
DB_HOST=127.0.0.1
DB_PORT=5432
EOF

chmod 600 /opt/gpu_monitor/.env
```

> **Important:** Generate a proper secret key instead of the placeholder:
> ```bash
> python3 -c "import secrets; print(secrets.token_urlsafe(50))"
> ```
> Then update `DJANGO_SECRET_KEY` in `.env`.

> **Note:** `DJANGO_ALLOWED_HOSTS=*` accepts requests from any IP address. This is
> suitable for local testing but should be set to your actual domain in production.

### 3.5 Run Migrations

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python manage.py migrate
python manage.py collectstatic --noinput
```

> **What is `set -a && source .env && set +a`?** It exports all variables from `.env` into the current shell environment so Django settings can read them via `os.environ.get()`.

Expected migration output (20 migrations):

```
Operations to perform:
  Apply all migrations: accounts, admin, audit, auth, contenttypes, metrics_app, rigs, sessions
Running migrations:
  Applying contenttypes.0001_initial... OK
  Applying auth.0001_initial... OK
  ...
  Applying accounts.0001_initial... OK
  Applying audit.0001_initial... OK
  Applying metrics_app.0001_initial... OK
  Applying rigs.0001_initial... OK
  Applying sessions.0001_initial... OK
```

### 3.6 Create an Admin User

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python manage.py createsuperuser
```

Enter email, username, and password when prompted. You will use these to log into the dashboard.

### 3.7 Configure Nginx

Create `/etc/nginx/sites-available/gpu_monitor`:

```nginx
server {
    listen 80;
    server_name localhost;

    client_max_body_size 2m;

    location /static/ {
        alias /opt/gpu_monitor/staticfiles/;
        expires 1d;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable the site:

```bash
sudo ln -sf /etc/nginx/sites-available/gpu_monitor /etc/nginx/sites-enabled/gpu_monitor
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx
sudo systemctl enable nginx
```

### 3.8 Start Gunicorn

**For persistent running (recommended), create a systemd service:**

```bash
sudo tee /etc/systemd/system/gunicorn.service << 'EOF'
[Unit]
Description=GPU Rig Monitor - Gunicorn
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=notify
User=root
Group=root
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/gunicorn \
    gpu_monitor.wsgi:application \
    --bind 127.0.0.1:8000 \
    --workers 4 \
    --timeout 30 \
    --access-logfile /opt/gpu_monitor/logs/gunicorn-access.log \
    --error-logfile /opt/gpu_monitor/logs/gunicorn-error.log \
    --log-level info
ExecReload=/bin/kill -s HUP $MAINPID
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable gunicorn
sudo systemctl start gunicorn
```

> **Note:** The `--access-logfile` and `--error-logfile` flags tell Gunicorn to write HTTP access logs and error logs to files. The `--log-level info` flag ensures worker start/stop events are logged. Without these flags, Gunicorn output only goes to the systemd journal (`journalctl -u gunicorn`).

**For foreground testing** (stops when you close the terminal):

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
gunicorn gpu_monitor.wsgi:application --bind 127.0.0.1:8000 --workers 2 --timeout 30
```

### 3.9 Quick Alternative: Django Dev Server

For quick testing without Nginx and Gunicorn:

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python manage.py runserver 0.0.0.0:8000
```

Then access the dashboard at `http://localhost:8000/`. This is **not suitable for testing the agent** (which sends to port 80) but works for verifying migrations and templates.

---

## 4. Agent Setup

The agent runs on each machine you want to monitor. For local testing, install it on the same machine.

### 4.1 Create Agent User and Directories

```bash
# Create the system user (no login shell for security)
sudo useradd --system --no-create-home --shell /usr/sbin/nologin monitoring-agent 2>/dev/null || true

# Create required directories
sudo mkdir -p /opt/monitoring-agent /etc/monitoring-agent /var/log/monitoring-agent

# Log directory must be writable by the agent user
sudo chown monitoring-agent:monitoring-agent /var/log/monitoring-agent
```

### 4.2 Install Agent Dependencies

```bash
# Create virtualenv for the agent
sudo python3 -m venv /opt/monitoring-agent/venv
sudo /opt/monitoring-agent/venv/bin/pip install --upgrade pip
sudo /opt/monitoring-agent/venv/bin/pip install psutil py-cpuinfo requests pyyaml docker

# Try to install NVIDIA GPU support (will gracefully fail without GPU)
sudo /opt/monitoring-agent/venv/bin/pip install pynvml 2>/dev/null || \
    echo "INFO: pynvml not installed — GPU monitoring will be unavailable (expected without NVIDIA GPU)"
```

### 4.3 Copy Agent Files

```bash
sudo cp /home/qrv/workspace/GPU-Rig-Monitoring-Platform/agent/run.py /opt/monitoring-agent/run.py
sudo chmod +x /opt/monitoring-agent/run.py
```

### 4.4 Configure the Agent

Create the config file:

```bash
sudo tee /etc/monitoring-agent/config.yaml << 'EOF'
rig_uuid: "auto"
rig_name: ""
api_key: "PASTE_YOUR_API_KEY_HERE"
server_endpoint: "http://localhost"
expected_gpu_count: 0
collection_timeout_s: 45
retry_attempts: 3
debug_mode: true
EOF
```

**Edit the config** to paste the API key you will create in Step 5:

```bash
sudo nano /etc/monitoring-agent/config.yaml
```

**Fix file ownership** — the `monitoring-agent` user needs to read this file:

```bash
sudo chown monitoring-agent:monitoring-agent /etc/monitoring-agent/config.yaml
sudo chmod 600 /etc/monitoring-agent/config.yaml
```

**Config field reference:**

| Field | Description |
|-------|-------------|
| `rig_uuid` | `"auto"` generates a permanent UUID on first run. After the first successful run, check the file — the UUID will be persisted. |
| `rig_name` | Suggested initial name for this rig (e.g., `"gpu-server-01"`). Used **only once** during first registration. Leave empty to use the machine's hostname. After creation, rename via the dashboard — this value is ignored on subsequent updates. |
| `api_key` | Create this from the dashboard (Step 5). The key is shown only once. |
| `server_endpoint` | `http://localhost` for local testing (no trailing slash). |
| `expected_gpu_count` | `0` = auto-detect. Set to your actual GPU count to flag mismatches. |
| `collection_timeout_s` | Hard limit in seconds for metric collection + upload. Default 45s leaves margin in the 60s cron cycle. |
| `retry_attempts` | Retries on transient failures with exponential backoff (1s → 2s → 4s). |
| `debug_mode` | `true` enables verbose logging and disables gzip compression. Use for troubleshooting. |

### 4.5 Set Up Sudoers

The agent needs root access for disk SMART data and journal logs. These are read-only commands that cannot modify the system:

```bash
echo 'Defaults:monitoring-agent !authenticate
monitoring-agent ALL=(root) NOPASSWD: /usr/sbin/smartctl, /usr/bin/smartctl, /bin/journalctl, /usr/bin/journalctl, /usr/sbin/nvme, /usr/bin/nvme' | sudo tee /etc/sudoers.d/monitoring-agent
sudo chmod 440 /etc/sudoers.d/monitoring-agent
```

**Critical:** The `Defaults:monitoring-agent !authenticate` line is **required** for system users with `nologin` shell. Without it, PAM `pam_unix` authentication fails with:
```
pam_unix(sudo:auth): conversation failed
pam_unix(sudo:auth) auth could not identify password for [monitoring-agent]
```
even though `NOPASSWD` is set. The `!authenticate` default tells sudo to skip PAM entirely for this user.

**What each command does:**
- `smartctl`: Read disk SMART health data (HDD/SSD health metrics)
- `nvme`: Read NVMe drive health logs (NVMe-specific metrics)
- `journalctl`: Read system error logs (for the Errors tab)

**Note:** Both common binary paths are included (`/usr/sbin/` and `/usr/bin/`) for cross-distro compatibility. The agent calls `sudo journalctl` (not bare `journalctl`) to ensure it can read system-level error logs.

**Verify:**
```bash
sudo -l -U monitoring-agent
```
Should show the NOPASSWD rules without any password prompt.

**Security:** All three commands are read-only. The agent cannot modify disks, logs, or system state. If a command is missing (e.g., no NVMe drive), the agent logs a warning and continues.

**GPU monitoring** does NOT require root — `pynvml` reads from the NVIDIA driver interface which is accessible to all users.

### 4.6 Set Up Cron

```bash
echo '* * * * * monitoring-agent flock -n /var/lock/monitoring-agent.lock /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/run.py >> /var/log/monitoring-agent/cron.log 2>&1' | sudo tee /etc/cron.d/monitoring-agent
sudo chmod 644 /etc/cron.d/monitoring-agent
```

> **How `flock` works:** `flock -n` creates a lock file to prevent overlapping runs. If a previous agent run is still executing (e.g., network timeout), the next cron invocation skips gracefully instead of stacking up.

### 4.7 Test the Agent

```bash
sudo -u monitoring-agent /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/run.py
```

**Expected output (no GPU):**

```json
{"ts":"2026-06-01T17:35:04","level":"INFO","module":"main","msg":"Starting collection for rig a1b2c3d4-..."}
{"ts":"2026-06-01T17:35:11","level":"WARNING","module":"gpu","msg":"GPU collection failed: No module named 'pynvml'"}
```

The GPU warning is expected without an NVIDIA GPU. The agent should complete without errors.

**If you see `PermissionError: config.yaml`:** The file ownership is wrong. Run:
```bash
sudo chown monitoring-agent:monitoring-agent /etc/monitoring-agent/config.yaml
```

---

## 5. First Run Verification

### 5.1 Check Server Health

```bash
curl -s http://localhost/api/v1/health/ | python3 -m json.tool
```

Expected:

```json
{
    "status": "healthy",
    "version": "1.0.0",
    "uptime_s": 0,
    "db_connection": "ok",
    "active_rigs": 0
}
```

### 5.2 Log Into the Dashboard

1. Open your browser: **http://localhost/accounts/login/**
2. Log in with the superuser credentials from Step 3.5

### 5.3 Create an API Key

1. Click **API Keys** in the top navigation bar
2. Enter a descriptive name (e.g., `local-test-rig`)
3. Click **Create Key**
4. **Copy the displayed key immediately** — it is shown only once
5. Paste it into the agent config:
   ```bash
   sudo nano /etc/monitoring-agent/config.yaml
   # Replace PASTE_YOUR_API_KEY_HERE with the actual key
   sudo chown monitoring-agent:monitoring-agent /etc/monitoring-agent/config.yaml
   ```

### 5.4 Verify the Agent Appears on the Dashboard

1. Run the agent manually (Step 4.7) or wait up to 2 minutes for cron to trigger
2. Open **http://localhost/dashboard/rigs/**
3. Your rig should appear with a green **● Online** badge
4. Click the rig name to see the detail page with live metrics

### 5.5 Verify HTMX Live Polling

1. On the rig detail page, wait 30 seconds
2. Metrics should update without a page reload
3. The status badge should remain green

### 5.6 Dashboard Features

The rig detail page has three tabs:

| Tab | Description |
|-----|-------------|
| **Live Metrics** | Auto-refreshing cards showing CPU, memory, GPU, Docker, storage, and errors (30s HTMX polling) |
| **Historical Charts** | 7 individual charts showing 24-hour trends: GPU temperature, GPU utilization, GPU VRAM usage, GPU power draw, CPU utilization, CPU temperature, memory usage |
| **Errors** | Recent system errors from journalctl/Windows Event Log |

**GPU Model Name Display:** The fleet overview table shows cleaned GPU model names (e.g., "RTX 3060" instead of "NVIDIA GeForce RTX 3060"). Hover over the name to see the full model string. For multiple GPUs, each GPU is listed separately.

**Rig Status:** Rigs are automatically marked as:
- 🟢 **Online** — reported within last 2 minutes
- 🟡 **Stale** — not seen for 2–10 minutes
- 🔴 **Offline** — not seen for 10+ minutes

> **Note:** The status update cron job (Section 5.8) must be running for automatic status changes.

### 5.7 Set Up Data Retention

> **Important for local testing:** Without data retention, your local database will grow indefinitely. Even with a single test rig, data accumulates at ~15.7 MB/day (measured). Enable this before running extended tests.

Configure automated data compaction and cleanup:

```bash
# Add cron job for daily data cleanup (runs at 3 AM as qrv user)
echo '0 3 * * * qrv bash /opt/gpu_monitor/deploy/data_retention.sh >> /var/log/monitoring-agent/cleanup-cron.log 2>&1' | sudo tee /etc/cron.d/monitoring-data-cleanup
```

> **Note:** The cron job runs as `qrv` (not root). Ensure `/opt/gpu_monitor/logs/` is owned by `qrv`:
> ```bash
> sudo chown -R qrv:qrv /opt/gpu_monitor/logs/
> ```

This runs two commands daily:

1. **`compact_data`** — Single-phase aggregation of old data:
   - Data > 1 day old → 1-hour buckets (60× reduction)
   - Aggregation per metric: AVG (temperature, utilization, power), SUM (network bytes, error_count), LAST (model names, UUIDs)
   - Parent table compacted first; child tables after
   - FK-safe: parent rows referenced by children are excluded

2. **`cleanup_old_data`** — Deletes data older than 31 days:
   - Processes tables in dependency order (children first, parent last)
   - Deletes in batches of 10,000 rows to avoid long locks
   - Handles tables with non-standard primary keys (e.g., `metrics_latest_snapshot` uses `rig_uuid`)

**Storage impact:** Without compaction, 1,000 rigs would use ~487 GB/month. With compaction: ~23 GB/month (95% savings). For a single test rig: ~24 MB/month with compaction (31-day retention).

#### Manual Run (for testing)

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a

# Compact data (dry run first)
python manage.py compact_data --dry-run --verbose

# Compact data (actual)
python manage.py compact_data --verbose

# Cleanup old data (dry run first)
python manage.py cleanup_old_data --dry-run --days=31 --verbose

# Cleanup old data (actual)
python manage.py cleanup_old_data --days=31 --verbose
```

#### Command Options

**compact_data:**
| Flag | Description |
|---|---|
| `--dry-run` | Preview without making changes |
| `--verbose` | Show per-table row counts |

**cleanup_old_data:**
| Flag | Description |
|---|---|
| `--days N` | Delete data older than N days (default: 31) |
| `--dry-run` | Preview without making changes |
| `--verbose` | Show per-table row counts |

#### Troubleshooting

**Check cron job is running:**
```bash
cat /etc/cron.d/monitoring-data-cleanup
tail -f /var/log/monitoring-agent/cleanup-cron.log
```

**Check if data is being compacted:**
```bash
cd /opt/gpu_monitor
source venv/bin/activate && set -a && source .env && set +a
python -c "
import os; os.environ['DJANGO_SETTINGS_MODULE'] = 'gpu_monitor.settings'
import django; django.setup()
from django.db import connection
for t in ['metrics_metricsnapshot', 'metrics_gpumetric', 'metrics_storagemetric',
          'metrics_networkmetric']:
    with connection.cursor() as c:
        c.execute(f'SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {t}')
        row = c.fetchall()[0]
        print(f'{t}: {row[0]:,} rows, {row[1]} to {row[2]}')
"
```

**Manual cleanup if cron failed:**
```bash
cd /opt/gpu_monitor
source venv/bin/activate && set -a && source .env && set +a
python manage.py compact_data --verbose
python manage.py cleanup_old_data --days=31 --verbose
```

#### Backfill (Test Data Generation)

For testing charts and data retention, the `backfill_historical_data` command creates
historical data by repeating recent data with shifted timestamps:

```bash
# Preview what will be inserted
python manage.py backfill_historical_data --dry-run

# Full 32-day backfill with 12-hour source window
python manage.py backfill_historical_data --hours 12 --days 32

# Custom: 6-hour source, 14 days target
python manage.py backfill_historical_data --hours 6 --days 14
```

**Options:**
| Flag | Description |
|---|---|
| `--hours N` | Source data window in hours (default: 9) |
| `--days N` | Target number of days to fill (default: 32) |
| `--dry-run` | Preview without inserting data |

**⚠️ Important:** After backfill, verify child data was inserted correctly:
```bash
python -c "
from metrics_app.models import MetricSnapshot, GPUMetric
from django.utils import timezone
from datetime import timedelta
cutoff = timezone.now() - timedelta(hours=1)
snaps = MetricSnapshot.objects.filter(timestamp__lt=cutoff).count()
gpus = GPUMetric.objects.filter(timestamp__lt=cutoff).count()
print(f'Snapshots: {snaps:,}, GPU rows: {gpus:,}, ratio: {gpus/max(snaps,1):.2f}')
print('Expected ratio: ~3.0 for multi-GPU rigs (should be > 1.0)')
"
```

**To remove backfilled data:**
```bash
# Delete everything older than the source window
python manage.py cleanup_old_data --days=0 --verbose
```

**Permission denied on logs:**
```bash
sudo chown -R qrv:qrv /opt/gpu_monitor/logs/
```

---

### 5.8 Set Up Rig Status Monitoring Cron

The platform needs a periodic task to mark rigs as **Stale** (not seen in 2–10 minutes) or **Offline** (not seen in 10+ minutes). Create the wrapper script and cron job:

```bash
# Copy the wrapper script to /opt
sudo cp gpu_monitor/deploy/update_rig_status.sh /opt/gpu_monitor/deploy/update_rig_status.sh
sudo chmod +x /opt/gpu_monitor/deploy/update_rig_status.sh

# Create the cron job
echo '*/2 * * * * root bash /opt/gpu_monitor/deploy/update_rig_status.sh' | sudo tee /etc/cron.d/rig-status

# Restart cron to pick up the new job
sudo systemctl restart cron
```

This runs every 2 minutes. Verify it's working:
```bash
cat /etc/cron.d/rig-status
# Wait up to 2 minutes, then check the log:
tail -f /opt/gpu_monitor/logs/rig_status.log
```

You should see output like `Updated: 0 stale, 2 offline`. If you see `password authentication failed`, the wrapper is not sourcing `.env` correctly — ensure the script contains `set -a && source .env && set +a` before the `python manage.py` call.

> **Important:** Without this cron job, rigs will always show "Online" even after they stop reporting. The `update_rig_status` management command checks `last_seen` timestamps and updates the status accordingly.
> **Note:** The wrapper script uses `bash` explicitly because inline `source` doesn't work in cron's default `/bin/sh` shell. The wrapper must source `.env` with `set -a && source .env && set +a` **before** calling `python manage.py` — Django reads DB credentials from `os.environ`, and without sourcing `.env` the DB password is empty, causing `password authentication failed`.

---

## 6. Understanding the File Layout

### Server (`/opt/gpu_monitor/`)

```
/opt/gpu_monitor/
├── venv/                       # Python virtual environment
├── gpu_monitor/                # Django project settings
│   ├── settings.py             # Main settings (reads from .env)
│   ├── urls.py                 # Root URL configuration
│   └── wsgi.py                 # WSGI entry point
├── accounts/                   # User auth + API key management
│   ├── models.py               # User, ApiKey models
│   ├── authentication.py       # X-API-Key auth for agents
│   └── views.py                # Login, logout, API key UI
├── rigs/                       # Rig inventory
│   ├── models.py               # Rig, RigTag models
│   └── management/commands/    # update_rig_status command
├── metrics_app/                # Ingestion API + metric storage
│   ├── models.py               # MetricSnapshot (timeseries), GPUMetric (timeseries), StorageMetric (timeseries), NetworkMetric (timeseries), DockerContainerMetric (timeseries), LatestDockerContainer (latest), LatestSnapshot (denormalized display cache — GPU/Storage/Network JSON arrays), RigStatusEvent |
│   ├── serializers.py          # Payload validation + processing
│   └── views.py                # IngestView, HealthView, ChartDataView, RigMetricsView
├── dashboard/                  # HTMX dashboard views
│   ├── views.py                # rig_list, rig_detail, htmx_metrics
│   ├── urls.py
│   └── templatetags/
|       └── gpu_filters.py      # Template filters: gpu_model_name, gpu_model_short, gpu_compact_summary, gpu_temp_cell, gpu_util_cell, gpu_fan_cell, time_since, last_seen_short |
├── audit/                      # Audit logging
│   ├── models.py               # AuditLog model
│   └── middleware.py           # Request audit middleware
├── templates/                  # Django HTML templates
│   └── dashboard/
│       ├── rig_detail.html     # Rig detail with tabs (Live Metrics, Historical Charts, Errors)
│       ├── _metrics_cards.html # Live metric cards
│       └── _rig_table.html     # Fleet overview table
├── scripts/                    # Dev/test helper scripts
│   ├── sync_to_opt.sh          # Full workspace → /opt deployment
│   ├── sync_agent.sh           # Agent files only → /opt deployment
│   └── sync_and_migrate.sh     # Granular file sync + migrate
├── logs/                       # Application logs
│   ├── app.log                 # Django structured JSON log
│   ├── gunicorn-access.log     # Gunicorn access log
│   └── gunicorn-error.log      # Gunicorn error log
├── staticfiles/                # Collected static files (served by Nginx)
├── .env                        # Environment variables (mode 600)
└── manage.py                   # Django management command
```

### Agent (`/opt/monitoring-agent/`)

```
/opt/monitoring-agent/
├── venv/                       # Python virtual environment
└── run.py                      # Agent script

/etc/monitoring-agent/
└── config.yaml                 # Agent configuration (mode 600)

/var/log/monitoring-agent/
├── agent.log                   # Structured JSON agent log (rotated 10MB x 3)
├── payload.json                # Latest full JSON payload sent to server (overwritten each run)
└── cron.log                    # Cron output log

/etc/cron.d/monitoring-agent   # Cron job definition
/etc/sudoers.d/monitoring-agent # Sudo permissions for disk/log access
```

### Project Repository (`GPU-Rig-Monitoring-Platform/`)

```
GPU-Rig-Monitoring-Platform/
├── agent/                      # Agent source code
│   ├── run.py                  # Agent script
│   ├── install.sh              # Agent installer (for production rigs)
│   └── config.yaml.example     # Config template
├── gpu_monitor/                # Django server source code
│   ├── deploy/
│   │   ├── server_install.sh   # Production server installer
│   │   ├── nginx.conf          # Production Nginx config (TLS)
│   │   └── gunicorn.service    # Production systemd unit
│   └── ...
├── docs/
│   ├── DEPLOYMENT_GUIDE.md     # Production deployment guide
│   └── LOCAL_DEPLOYMENT_GUIDE.md # This guide
└── README.md
```

---

## 7. Troubleshooting

### Server Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| `502 Bad Gateway` | Gunicorn not running | `sudo systemctl restart gunicorn` |
| `500 Internal Server Error` | Django config error | Check `/opt/gpu_monitor/logs/gunicorn-error.log` |
| `FileNotFoundError: logs/app.log` | Missing `logs/` directory | `mkdir -p /opt/gpu_monitor/logs` |
| `ValueError: Dependency on app with no migrations` | Migration files missing | `python manage.py makemigrations accounts rigs metrics_app dashboard audit` then `python manage.py migrate` |
| `psycopg2.OperationalError: password authentication failed` | Wrong DB password | Reset: `sudo -u postgres psql -c "ALTER USER gpu_monitor PASSWORD 'local_dev_password';"` — ensure `.env` matches |
| `psycopg2.OperationalError: connection refused` | PostgreSQL not running | `sudo systemctl restart postgresql` |
| `collectstatic` fails | Missing `staticfiles/` dir | `mkdir -p /opt/gpu_monitor/staticfiles` then re-run |
|| Nginx `403 Forbidden` on static files | Permission issue | `chmod 755 /opt/gpu_monitor/staticfiles` |
|| `ALLOWED_HOSTS` error | Host not in allowed list | Add your IP/hostname to `DJANGO_ALLOWED_HOSTS` in `.env` |
|| `PermissionError` when rendering template | New template file has restrictive permissions | `sudo chmod -R 644 /opt/gpu_monitor/templates/` |
|| Dashboard shows "Internal Server Error" after adding new files | Template or view file not readable by Gunicorn | Check file permissions: `sudo chmod 644 /opt/gpu_monitor/templates/dashboard/*.html` |

### Agent Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| `PermissionError: config.yaml` | File owned by root | `sudo chown monitoring-agent:monitoring-agent /etc/monitoring-agent/config.yaml` |
| `AttributeError: module 'logging' has no attribute 'handlers'` | Missing import in `run.py` | Ensure `import logging.handlers` is present after `import logging` |
| `401 Unauthorized` | Invalid API key | Regenerate key on dashboard, update `config.yaml` |
| `Connection refused` | Server not running | `curl -v http://localhost/api/v1/health/` |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Using HTTPS without cert | Use `http://localhost` for local testing |
| `GPU collection failed: No module named 'pynvml'` | No NVIDIA GPU | Expected — GPU metrics will be empty |
| `smartctl: command not found` | Disk tools not installed | `sudo apt install smartmontools nvme-cli` |
| Agent hangs / overlaps | Stale lock file | `sudo rm -f /var/lock/monitoring-agent.lock` |
| Agent runs but rig doesn't appear | API key not set | Create API key on dashboard, update `config.yaml` |

### Reading Agent Logs

Agent logs are structured JSON. Use `jq` for readable output:

```bash
# Pretty-print agent logs
tail -20 /var/log/monitoring-agent/agent.log | jq .

# Filter for errors only
grep '"level":"WARNING"' /var/log/monitoring-agent/agent.log | jq .

# Watch live
tail -f /var/log/monitoring-agent/agent.log | jq .
```

---

## 8. Differences from Production

| Aspect | Production | This Local Setup |
|--------|-----------|------------------|
| **TLS** | Let's Encrypt (port 443) | HTTP only (port 80) |
| **Domain** | `monitor.example.com` | `localhost` |
| **Database** | PostgreSQL | Plain PostgreSQL |
| **Gunicorn user** | Dedicated `monitoring` user | `root` (or current user) |
| **Agent** | Separate rigs via HTTPS | Same machine via HTTP |
| **Nginx** | Rate limiting, HSTS, CSP headers | Basic proxy only |
| **Firewall** | UFW with strict rules | Not configured |
| **Backup** | Daily `pg_dump` + rclone | Not configured |
| **Meta-monitoring** | UptimeRobot | Not configured |
|| **Data retention** | compact_data + cleanup_old_data (31-day retention with tiered compaction) | Same (compact_data + cleanup_old_data) |

---

## 9. Preparing for Production Deployment

When you are ready to deploy to a real server:

1. **Enable data retention** — Add cron job for `data_retention.sh` (see Section 5.7)
2. **Configure a domain name** and point DNS A record to your server
3. **Enable TLS** with `certbot --nginx -d yourdomain.com`
4. **Set up UFW firewall** — allow only ports 22, 80, 443
5. **Configure daily backups** with `pg_dump` + rclone to offsite storage
6. **Deploy agents on separate rigs** — each rig gets its own API key
7. **Set `DJANGO_DEBUG=False`** and generate a proper `DJANGO_SECRET_KEY`
8. **Create a dedicated `monitoring` user** for Gunicorn instead of running as root
9. **Enable rate limiting** in Nginx (see `deploy/nginx.conf` in the repository)

See `docs/DEPLOYMENT_GUIDE.md` for the full production deployment procedure.

---

## 10. Stopping, Restarting, and Upgrading

```bash
# Check service status
systemctl status gunicorn
systemctl status nginx
systemctl status postgresql

# Restart after code changes
sudo systemctl restart gunicorn

# View logs in real-time
tail -f /opt/gpu_monitor/logs/gunicorn-error.log    # Gunicorn errors, worker crashes
tail -f /opt/gpu_monitor/logs/gunicorn-access.log  # HTTP access log (requests, status codes)
tail -f /opt/gpu_monitor/logs/app.log               # Django structured JSON log

# Agent logs
tail -f /var/log/monitoring-agent/agent.log          # Structured JSON agent log
tail -f /var/log/monitoring-agent/cron.log           # Cron output log

# After pulling code changes, re-run migrations
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python manage.py migrate
python manage.py collectstatic --noinput

# Fix permissions on any new template/view files
sudo chmod -R 644 /opt/gpu_monitor/templates/
sudo chmod -R 755 /opt/gpu_monitor/templates/dashboard/

sudo systemctl restart gunicorn
```
