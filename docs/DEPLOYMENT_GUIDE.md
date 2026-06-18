# GPU Rig Monitoring Platform — Deployment Guide

**Version:** 1.2
**Target OS:** Ubuntu 22.04 / 24.04 LTS (single VPS with domain name)

This guide deploys the GPU Rig Monitoring Platform on a **production VPS** with TLS, a domain name, and remote GPU rigs sending telemetry.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [DNS Setup](#3-dns-setup)
4. [Server Deployment](#4-server-deployment)
   - 4.1 [Provision the VPS](#41-provision-the-vps)
   - 4.2 [Configure Cloud Firewall](#42-configure-cloud-firewall)
   - 4.3 [Domain and DNS](#43-domain-and-dns)
   - 4.4 [Run the Install Script](#44-run-the-install-script)
   - 4.5 [Save the Database Password](#45-save-the-database-password)
   - 4.6 [Create an Admin User](#46-create-an-admin-user)
   - 4.7 [Set Up Data Retention](#47-set-up-data-retention)
   - 4.8 [Verify the Deployment](#48-verify-the-deployment)
5. [Rig Agent Deployment](#5-rig-agent-deployment)
   - 5.1 [Prerequisites per Rig](#51-prerequisites-per-rig)
   - 5.2 [Get an API Key](#52-get-an-api-key)
   - 5.3 [Transfer and Install](#53-transfer-and-install)
   - 5.4 [Configure the Agent](#54-configure-the-agent)
   - 5.5 [Test the Agent](#55-test-the-agent)
   - 5.6 [Verify on Dashboard](#56-verify-on-dashboard)
6. [Post-Deployment Configuration](#6-post-deployment-configuration)
   - 6.1 [Rig Status Monitoring Cron](#61-rig-status-monitoring-cron)
   - 6.2 [Dashboard Features](#62-dashboard-features)
   - 6.3 [Log Rotation](#63-log-rotation)
   - 6.4 [Database Backups](#64-database-backups)
   - 6.5 [TLS Certificate Renewal](#65-tls-certificate-renewal)
   - 6.6 [External Monitoring](#66-external-monitoring)
7. [Upgrading](#7-upgrading)
8. [Troubleshooting](#8-troubleshooting)
9. [File Locations Reference](#9-file-locations-reference)
10. [Security Hardening Checklist](#10-security-hardening-checklist)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        RIG FLEET (Untrusted)                            │
│                                                                         │
│  ┌─────────────────┐    HTTPS POST /api/v1/ingest/    ┌──────────────┐  │
│  │ Python Client   │ ────────────────────────────────→ │   Nginx      │  │
│  │ Agent (cron 60s)│                                   │   Reverse    │  │
│  └─────────────────┘                                   │   Proxy      │  │
│                                                        └──────┬───────┘  │
└───────────────────────────────────────────────────────────────┼──────────┘
                                                                │ HTTPS
                                                                │ (TLS 1.3)
┌───────────────────────────────────────────────────────────────┼──────────┐
│                   SINGLE UBUNTU VPS (Trusted)                 │          │
│                                                               ▼          │
│  ┌─────────────────┐    TCP/5432    ┌──────────────────────────────┐    │
│  │ Django + DRF    │ ────────────→  │ PostgreSQL     │    │
│  │ (Gunicorn)      │                │                              │    │
│  └────────┬────────┘                └──────────────────────────────┘    │
│           │                                                             │
│           │ Render/Query                                                 │
│           ▼                                                             │
│  ┌─────────────────┐    HTTPS GET/POST    ┌──────────────────────────┐  │
│  │ HTMX Dashboard  │ ←─────────────────── │   User Browser           │  │
│  │ UI              │    + HTMX Polling     │                          │  │
│  └─────────────────┘                       └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

**Production infrastructure baseline:**

| Resource | Specification |
|----------|--------------|
| OS | Ubuntu 22.04 / 24.04 LTS |
| Compute | 4–8 vCPU |
| RAM | 16–32 GB |
| Storage | 500 GB+ NVMe SSD (NVMe required for write IOPS) |
| Network | 1 public IPv4, DNS A record |
| TLS | Let's Encrypt (certbot), auto-renew via systemd timer |

---

## 2. Prerequisites

Before starting, ensure you have:

- [ ] A VPS provisioned with Ubuntu 22.04 or 24.04, root SSH access
- [ ] A registered domain name (e.g., `example.com`)
- [ ] DNS A record pointing `monitor.example.com` → your VPS IP
- [ ] An SSH key pair for secure access
- [ ] The project files on the VPS (via `git clone` or `rsync`)

### VPS Provider Examples

| Provider | Example Plan | Cost |
|----------|-------------|------|
| Hetzner (EU, cost-effective) | CX41 (4 vCPU, 16 GB) | ~€15/mo |
| DigitalOcean (managed, global) | Basic Droplet (4 GB RAM+) | ~$24/mo |
| AWS EC2 | t3.xlarge (4 vCPU, 16 GB) | ~$120/mo |
| Linode | Dedicated 8 GB | ~$60/mo |

> **Why NVMe is mandatory:** High write IOPS from 1,000 rigs reporting every 60 seconds. SATA SSDs will bottleneck during compaction and vacuum operations, causing ingestion lag.

---

## 3. DNS Setup

### 3.1 Register a Domain

| Registrar | Approximate Cost (.com) |
|-----------|------------------------|
| Namecheap | ~$10/year |
| Cloudflare Registrar | ~$10/year (at cost) |
| Google Domains | ~$12/year |

Options for the monitoring dashboard:
- A subdomain: `monitor.yourcompany.com`
- A dedicated domain: `yourfleet.io`

### 3.2 Configure DNS

Create an **A record** pointing to your VPS public IP:

```
Type: A
Name: monitor
Value: 203.0.113.50      ← your VPS public IPv4
TTL: 300 (5 minutes during setup)
```

**Verify DNS propagation:**

```bash
dig monitor.example.com +short
# Should return: 203.0.113.50 (your VPS IP)
```

> **Tip:** Set TTL to 300 seconds during setup so changes propagate quickly. Increase to 3600+ later.

---

## 4. Server Deployment

### 4.1 Provision the VPS

**Option A — Transfer project files via rsync:**

```bash
# From your local machine:
rsync -avz --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
    /path/to/GPU-Rig-Monitoring-Platform/gpu_monitor/ \
    root@VPS_IP:/tmp/gpu_monitor/
```

**Option B — Clone from GitHub (no login required):**

```bash
# On the VPS (HTTPS clone — no GitHub account needed):
git clone https://github.com/dawmro/GPU-Rig-Monitoring-Platform.git /tmp/gpu_monitor

# If you want to clone only the latest commit (faster, less disk usage):
git clone --depth 1 https://github.com/dawmro/GPU-Rig-Monitoring-Platform.git /tmp/gpu_monitor
```

> **Note:** The repository is public. No GitHub account or SSH key is required for HTTPS cloning. Use `--depth 1` for a shallow clone that downloads only the latest commit — much faster and uses less disk space.

**Option C — Using scp:**

```bash
scp -r /path/to/gpu_monitor root@VPS_IP:/tmp/gpu_monitor
```

### 4.2 Configure Cloud Firewall

Many VPS providers have a **cloud firewall** in addition to UFW. Ensure these ports are open **before** running the install script:

| Direction | Port | Protocol | Purpose |
|-----------|------|----------|---------|
| Inbound | 22 | TCP | SSH |
| Inbound | 80 | TCP | HTTP (Let's Encrypt challenge) |
| Inbound | 443 | TCP | HTTPS |
| Outbound | all | all | Package downloads, Let's Encrypt |

**Provider-specific locations:**

- **AWS EC2:** Security Groups → Inbound Rules
- **GCP:** VPC Network → Firewall Rules
- **Hetzner:** Firewall in Cloud Console
- **DigitalOcean:** Networking → Firewalls
- **Linode:** Firewalls in Cloud Manager

> **Important:** If your provider blocks port 80 at the cloud level, certbot's HTTP-01 challenge will fail and TLS certificate acquisition won't work.

### 4.3 Domain and DNS

Ensure your DNS A record (Step 3.2) is propagated before continuing. The `server_install.sh` script will request a TLS certificate via Let's Encrypt, which requires port 80 to be reachable and DNS to resolve correctly.

Test from your local machine:

```bash
# DNS resolves?
dig monitor.example.com +short

# Port 80 reachable?
curl -I http://monitor.example.com

# HTTPS reachable (after deployment)?
curl -I https://monitor.example.com/api/v1/health/
```

### 4.4 Run the Install Script

```bash
# Move project into place
mv /tmp/gpu_monitor /opt/gpu_monitor

# Make the script executable
chmod +x /opt/gpu_monitor/deploy/server_install.sh

# Run it — pass your domain as the only argument
/opt/gpu_monitor/deploy/server_install.sh monitor.example.com
```

**The script performs these operations, in order:**

| Step | What It Does |
|------|-------------|
| 1 | Installs system packages (Python, PostgreSQL, Nginx, certbot, UFW) |
| 2 | Creates `gpu_monitor` DB user and database |
| 3 | Creates `monitoring` OS user (no-login shell) |
| 4 | Sets up Python virtualenv and installs dependencies |
| 5 | Writes `/opt/gpu_monitor/.env` with secrets and DB credentials |
| 6 | Runs Django migrations + `collectstatic` |
| 7 | Installs Gunicorn systemd unit and starts it |
| 8 | Installs Nginx site config, removes default site, restarts Nginx |
| 9 | Runs Certbot to obtain Let's Encrypt TLS certificate |
| 10 | Configures UFW firewall (allow 22/80/443) |
| 11 | Enables and starts all services |

### 4.5 Save the Database Password

The script prints the auto-generated database password. **Save it somewhere safe** (password manager). It is also saved to `/opt/gpu_monitor/.env`:

```bash
cat /opt/gpu_monitor/.env
```

Expected contents:

```
DJANGO_SECRET_KEY=random-secret-key-here
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=monitor.example.com
DB_NAME=gpu_monitor
DB_USER=gpu_monitor
DB_PASSWORD=your-random-password-here
DB_HOST=127.0.0.1
DB_PORT=5432

# Optional: uncomment for Gmail SMTP password recovery
# See Architecture doc §7.5 for setup instructions
# EMAIL_HOST=smtp.gmail.com
# EMAIL_PORT=587
# EMAIL_USE_TLS=true
# EMAIL_HOST_USER=youragent@gmail.com
# EMAIL_HOST_PASSWORD=*** efgh ijkl mnop
# DEFAULT_FROM_EMAIL=noreply@yourdomain.com
```

> **Email setup (optional):** For password recovery via Gmail SMTP, see §7.5 in the Architecture doc. Leave `EMAIL_HOST` commented out for development (emails print to console).

### 4.6 Create an Admin User

The script cannot create a superuser automatically (interactive prompts). Run:

```bash
sudo -u monitoring bash -c 'cd /opt/gpu_monitor && source venv/bin/activate && set -a && source .env && set +a && python manage.py createsuperuser'
```

Enter email, username, and password when prompted. Use the email to log into the dashboard.

### 4.7 Set Up Data Retention

Configure automated data compaction and cleanup. This is essential for long-term storage management — without it, the database grows indefinitely (~487 GB/month at 1,000 rigs). With compaction: ~23 GB/month (95% savings).

#### Quick Setup

```bash
# Add cron job for daily data cleanup (runs at 3 AM as qrv user)
echo '0 3 * * * qrv bash /opt/gpu_monitor/deploy/data_retention.sh >> /var/log/monitoring-agent/cleanup-cron.log 2>&1' | sudo tee /etc/cron.d/monitoring-data-cleanup
```

The cron job runs `data_retention.sh` which executes three steps:
1. `compact_data` — aggregate old data into 1-hour buckets
2. `cleanup_old_data` — delete data older than 31 days
3. `VACUUM ANALYZE` — reclaim dead tuples and update planner statistics

Alternatively, you can use the combined `daily_maintenance` command directly:
```bash
echo '0 3 * * * qrv cd /opt/gpu_monitor && source venv/bin/activate && set -a && source .env && set +a && python manage.py daily_maintenance --verbose >> /var/log/monitoring-agent/cleanup-cron.log 2>&1' | sudo tee /etc/cron.d/monitoring-data-cleanup
```

> **Note:** The cron job runs as `qrv` (not root). Ensure `/opt/gpu_monitor/logs/` is owned by `qrv`:
> ```bash
> sudo chown -R qrv:qrv /opt/gpu_monitor/logs/
> ```

#### What It Does

The `data_retention.sh` wrapper runs three steps daily:

1. **`compact_data`** — Single-phase aggregation of old data:
   - Data > 1 day old → 1-hour buckets (60× reduction)
   - Aggregation per metric: AVG (temperature, utilization, power), SUM (network bytes, error_count), LAST (model names, UUIDs)
   - Parent table (`metrics_metricsnapshot`) compacted first; child tables after
   - FK-safe: parent rows referenced by children are excluded from compaction

2. **`cleanup_old_data`** — Deletes data older than 31 days:
   - Processes tables in dependency order (children first, parent last)
   - Deletes in batches of 10,000 rows to avoid long table locks
   - 31 days provides 1-day safety margin beyond the 30-day max chart range
   - Handles tables with non-standard primary keys (e.g., `metrics_latest_snapshot` uses `rig_uuid`)

3. **`VACUUM ANALYZE`** — Reclaims dead tuples and updates query planner statistics:
   - Runs on all 5 metrics tables after compaction and cleanup
   - Uses regular `VACUUM ANALYZE` (not `VACUUM FULL`) — no exclusive lock, runs concurrently with production traffic
   - Reclaims space from dead tuples for reuse within the table
   - Updates planner statistics so query plans stay optimal after data distribution changes
   - Takes seconds per table (not minutes like VACUUM FULL)

> **Why not VACUUM FULL?** `VACUUM FULL` acquires an exclusive lock on each table, blocking all reads/writes for the duration. For large tables (millions of rows), this could block agent ingest for 30+ seconds, causing missed heartbeats and false "stale" alarms. Regular `VACUUM ANALYZE` runs concurrently with no blocking.

> **Why run it manually?** PostgreSQL's autovacuum daemon handles dead tuple cleanup eventually, but after bulk DELETE operations (from compact_data and cleanup_old_data), a manual `VACUUM ANALYZE` ensures immediate reclamation and fresh planner statistics.

#### Manual Run

```bash
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a

# Run all maintenance steps at once (recommended)
python manage.py daily_maintenance --verbose

# Or run individual steps:

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

**daily_maintenance:**
| Flag | Description |
|---|---|
| `--days N` | Retention period in days (default: 31) |
| `--dry-run` | Preview without making changes |
| `--verbose` | Show detailed per-table statistics |
| `--skip-compact` | Skip compaction step |
| `--skip-cleanup` | Skip cleanup step |
| `--skip-vacuum` | Skip vacuum analyze step |

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

#### Storage Impact

| Retention | Raw Storage (1,000 rigs) | After Compaction |
|---|---|---|
|| 1 day | 15.3 GB | 15.3 GB ||
|| 7 days | 107 GB | 22 GB ||
|| 31 days | 460 GB | ~23 GB ||

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

```

#### Backfill (Test Data Generation)

For testing charts and data retention, the `backfill_historical_data` command creates
historical data by repeating recent data with shifted timestamps:

```bash
# Preview what will be inserted
python manage.py backfill_historical_data --dry-run

# Full 32-day backfill with 12-hour source window
python manage.py backfill_historical_data --hours 12 --days 32
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
print(f'GPU/snap ratio: {gpus/max(snaps,1):.2f} (expected > 1.0)')
"
```

**To remove backfilled data:**
```bash
python manage.py cleanup_old_data --days=0 --verbose
```

**Permission denied on logs:**
```bash
# Fix ownership and permissions on the logs directory
sudo chown -R monitoring:monitoring /opt/gpu_monitor/logs/
sudo chmod 755 /opt/gpu_monitor/logs/
sudo chmod 664 /opt/gpu_monitor/logs/*.log
```

**Django migrate fails with "ValueError: Unable to configure handler 'file'":**
This happens when the log files don't exist or have wrong permissions against the user running manage.py.
```bash
# Create log files with correct ownership and permissions
sudo -u monitoring bash -c 'touch /opt/gpu_monitor/logs/app.log'
sudo chown monitoring:monitoring /opt/gpu_monitor/logs/app.log
sudo chmod 664 /opt/gpu_monitor/logs/app.log
# Then retry:
sudo -u monitoring bash -c 'cd /opt/gpu_monitor && source venv/bin/activate && set -a && source .env && set +a && python manage.py migrate'
```

**Gunicorn fails with "Permission denied" on log files:**
The `monitoring` user must own all log files. The systemd service runs Gunicorn as `User=monitoring`.
```bash
sudo chown -R monitoring:monitoring /opt/gpu_monitor/logs/
sudo chmod 755 /opt/gpu_monitor/logs/
sudo chmod 664 /opt/gpu_monitor/logs/*.log
```

### 4.8 Verify the Deployment

```bash
# All services active?
systemctl is-active gunicorn postgresql nginx

# Database responding?
sudo -u postgres psql -d gpu_monitor -c "SELECT 1"

# Health endpoint returns healthy?
curl -s https://monitor.example.com/api/v1/health/ | python3 -m json.tool

# Root URL redirects to login (unauthenticated)?
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://monitor.example.com/
# Expected: 302 https://monitor.example.com/accounts/login/

# Root URL redirects to dashboard (authenticated)?
# Log in via browser first, then:
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://monitor.example.com/ --cookie "sessionid=YOUR_SESSION_COOKIE"
# Expected: 302 https://monitor.example.com/dashboard/rigs/
```

Expected health response:

```json
{
    "status": "healthy",
    "version": "1.0.0",
    "uptime_s": 0,
    "db_connection": "ok",
    "active_rigs": 0
}
```

### 4.9 Log In to the Dashboard

Open your browser and navigate to:

```
https://monitor.example.com/
```

The root URL (`/`) automatically redirects:
- **Authenticated users** → `/dashboard/rigs/` (fleet overview)
- **Unauthenticated users** → `/accounts/login/` (login page)

You can also navigate directly to `/accounts/login/`.

Log in with the admin credentials from Step 4.6. You should see the fleet overview page with no rigs yet.

---

## 5. Rig Agent Deployment

Deploy the agent on **each GPU rig** you want to monitor.

### 5.1 Prerequisites per Rig

| Requirement | Details |
|-------------|---------|
| **OS** | Linux (Ubuntu 20.04+, Debian 11+, or similar) |
| **Python** | 3.10+ |
| **Network** | HTTPS access to `https://monitor.example.com` |
| **Privileges** | Root/sudo access for installation |
| **NVIDIA GPUs** | `nvidia-smi` must be available for GPU monitoring |

### 5.2 Get an API Key

1. Log in to `https://monitor.example.com/accounts/login/`
2. Click **API Keys** in the top navigation bar
3. Enter a descriptive name (e.g., `rig-farm-01-node-3`) and click **Create Key**
4. **Copy the displayed API key immediately** — it is shown only once
5. Keep this key ready for Step 5.4

### 5.3 Transfer and Install

**Transfer agent files to the rig:**

```bash
# From your local machine or the server:
rsync -avz /path/to/agent/ root@RIG_IP:/tmp/agent/
```

**Run the installer on the rig:**

```bash
ssh root@RIG_IP

# Create install directory and copy files
mkdir -p /opt/monitoring-agent
cp /tmp/agent/run.py /opt/monitoring-agent/run.py

# Run the installer
chmod +x /tmp/agent/install.sh
/tmp/agent/install.sh
```

**The script performs these operations:**

| Step | What It Does |
|------|-------------|
| 1 | Creates `monitoring-agent` system user (no-login shell) |
| 2 | Creates directories: `/opt/monitoring-agent/`, `/etc/monitoring-agent/`, `/var/log/monitoring-agent/` |
| 3 | Creates Python virtualenv and installs dependencies |
| 4 | Copies `run.py` and creates config template |
| 5 | Configures sudoers for SMART disk queries |
| 6 | Creates cron job (every 60 seconds, with `flock` to prevent overlaps) |

**Agent permissions (what the agent needs and why):**

The `monitoring-agent` system user runs without root privileges but needs elevated access for specific hardware queries:

| Command | Purpose | Risk |
|---------|---------|------|
| `/usr/sbin/smartctl` or `/usr/bin/smartctl` | Read disk SMART health data (SATA) | Read-only, no disk modification |
| `/usr/sbin/nvme` or `/usr/bin/nvme` | Read NVMe drive health/temperature | Read-only, no disk modification |
| `/bin/journalctl` or `/usr/bin/journalctl` | Read system error logs | Read-only, no log modification |

These are granted via `/etc/sudoers.d/monitoring-agent`:
```
Defaults:monitoring-agent !authenticate
monitoring-agent ALL=(root) NOPASSWD: /usr/sbin/smartctl, /usr/bin/smartctl, /bin/journalctl, /usr/bin/journalctl, /usr/sbin/nvme, /usr/bin/nvme
```

**Critical:** The `Defaults:monitoring-agent !authenticate` line is **required**. Without it, PAM authentication fails for the `monitoring-agent` system user (which has `nologin` shell and no password), producing these errors in system logs:
```
pam_unix(sudo:auth): conversation failed
pam_unix(sudo:auth) auth could not identify password for [monitoring-agent]
```
The `!authenticate` default tells sudo to skip PAM entirely for this user. The `NOPASSWD` tag alone is insufficient.

**Security properties:**
- `!authenticate`: Skip PAM entirely (required for nologin system users)
- `NOPASSWD`: No password required (agent runs non-interactively via cron)
- Command whitelist: Only these commands are allowed, nothing else
- Read-only: All commands only read system state, never modify it
- If any command is missing (e.g., no NVMe drive), the agent logs a warning and continues
- Both common binary paths are included (`/usr/sbin/` and `/usr/bin/`) for cross-distro compatibility

**GPU monitoring** does NOT require root — `pynvml` reads from the NVIDIA driver interface which is accessible to all users.

**Note:** The agent calls `sudo journalctl` (not bare `journalctl`) to ensure it can read system-level error logs. The sudoers config above allows this without a password prompt.

**Verify:**
```bash
sudo -l -U monitoring-agent
```
Should show the NOPASSWD rules. If it shows "may not run sudo", re-create the sudoers file.

### 5.4 Configure the Agent

Edit the config file on the rig:

```bash
nano /etc/monitoring-agent/config.yaml
```

Set these values:

```yaml
rig_uuid: "auto"
rig_name: ""
api_key: "PASTE_YOUR_API_KEY_HERE"
server_endpoint: "https://monitor.example.com"
expected_gpu_count: 0
collection_timeout_s: 45
retry_attempts: 3
debug_mode: false
```

**Config field reference:**

| Field | Description |
|-------|-------------|
| `rig_uuid` | `"auto"` generates a permanent UUID on first run. After the first successful run, check the file — the UUID will be persisted. |
| `rig_name` | Suggested initial name for this rig (e.g., `"gpu-server-01"`). Used **only once** during first registration. Leave empty to use the machine's hostname. After creation, rename via the dashboard — this value is ignored on subsequent updates. |
| `api_key` | The exact key copied from the server dashboard. No quotes needed unless the key contains special characters. |
| `server_endpoint` | Your server's HTTPS URL **without** a trailing slash. |
| `expected_gpu_count` | `0` for auto-detect. Set to your actual GPU count (e.g., `4`) to flag mismatches. |
| `collection_timeout_s` | Hard limit in seconds for metric collection + upload. Default 45s leaves 15s buffer within the 60s cron cycle. |
| `retry_attempts` | Retries on transient failures with exponential backoff (1s → 2s → 4s). |
| `debug_mode` | `false` for production. Set `true` temporarily for troubleshooting (disables gzip, enables verbose logging). |

### 5.5 Test the Agent

```bash
sudo -u monitoring-agent /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/run.py
```

**Expected output:**

```json
{"ts":"2026-05-20T14:32:00","level":"INFO","module":"main","msg":"Starting collection for rig a1b2c3d4-..."}
{"ts":"2026-05-20T14:32:02","level":"INFO","module":"transport","msg":"Ingest response: 200 {\"status\": \"new\"}"}
{"ts":"2026-05-20T14:32:02","level":"INFO","module":"main","msg":"Payload accepted: new"}
```

### 5.6 Verify on the Dashboard

1. Open `https://monitor.example.com/dashboard/rigs/`
2. Your rig should appear within **2 minutes** (1 minute for cron to trigger + collection time)
3. The status badge shows **● Online** (green) once the first payload is received
4. Click the rig name to see the **detail page** with live metrics (CPU, GPU, memory, Docker, storage, errors) — all refreshing every 30 seconds via HTMX

---

## 6. Post-Deployment Configuration

### 6.1 Rig Status Monitoring Cron

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

This runs every 2 minutes as recommended by the architecture specification.

> **Important:** Without this cron job, rigs will always show "Online" even after they stop reporting.
>
> **Note:** The wrapper script uses `bash` explicitly because inline `source` doesn't work in cron's default `/bin/sh` shell. The wrapper must source `.env` with `set -a && source .env && set +a` **before** calling `python manage.py` — Django reads DB credentials from `os.environ`, and without sourcing `.env` the DB password is empty, causing `password authentication failed`.

### 6.2 Dashboard Features

The rig detail page has three tabs:

| Tab | Description |
|-----|-------------|
|| **Live Metrics** | Auto-refreshing cards showing CPU, memory, GPU (with index), GPU Processes (per-process: name, type badge, memory), Docker, storage, and errors (30s HTMX polling) |
|| **Historical Charts** | Combined charts: GPU (Temp/Util/Memory/Power/Fan — multi-GPU), CPU (Util/Temp/Load Avg), Memory & Swap (combined), Disk Usage (multi-disk), Network Traffic (RX/TX/Errors combined), Container CPU/Memory, Uptime, Error Frequency. Refresh via ↻ button |
| **Errors** | Recent system errors from journalctl/Windows Event Log |

**Fleet Overview Table:**
- Shows **all GPUs** per rig (not just GPU 0)
- GPU column: compact model summary with count (e.g., "RTX 3060 ×8")
- GPU Temp/Util/Fan columns: space-separated color-coded values, one per GPU
- Hover tooltips show per-GPU breakdown
- Units in column headers (e.g., "GPU Temp [°C]") — no inline units in cells
- **Tag filter** dropdown to filter rigs by tag
- Tags displayed as colored pills per rig

**Rig Detail Page:**
- Tags displayed below rig UUID with add/remove capability
- GPU index shown before model name (e.g., "GPU0: RTX 2060 SUPER")
- GPU Processes section showing per-process: name, type badge (C/G/C+G), memory usage

**Tag Management:**
- Create, edit, delete tags from dashboard (/accounts/tags/)
- Tags have name and color
- Assign/remove tags on rig detail page
- Filter fleet overview by tag

**Rig Status:** Rigs are automatically marked as:
- 🟢 **Online** — reported within last 2 minutes
- 🟡 **Stale** — not seen for 2–10 minutes
- 🔴 **Offline** — not seen for 10+ minutes

**Rate Limiting:**
- Per-rig rate limit: 5 req/min per rig_uuid (each rig gets its own budget)
- Per-IP rate limit: 30 req/s (burst protection)
- Timestamp validation: payloads with timestamps >5 min future or >1 hour past are rejected (400)
- Agents send both `X-API-Key` (user auth) and `X-Rig-UUID` (rig identification) headers

### 6.3 Log Rotation

Configure `logrotate` for application logs:

```bash
sudo tee /etc/logrotate.d/gpu-monitor << 'EOF'
/opt/gpu_monitor/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    copytruncate
}

/var/log/monitoring-agent/*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
    copytruncate
}
EOF
```

### 6.4 Database Backups

**Create a backup script** at `/opt/gpu_monitor/deploy/backup_db.sh`:

```bash
cat > /opt/gpu_monitor/deploy/backup_db.sh << 'SCRIPT'
#!/bin/bash
set -euo pipefail

BACKUP_DIR="/var/backups/postgres"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

# Create compressed backup
sudo -u postgres pg_dump -Fc gpu_monitor > "$BACKUP_DIR/gpu_monitor_${DATE}.dump"
gzip "$BACKUP_DIR/gpu_monitor_${DATE}.dump"

# Remove old backups
find "$BACKUP_DIR" -name "*.dump.gz" -mtime "+${RETENTION_DAYS}" -delete

echo "Backup complete: $BACKUP_DIR/gpu_monitor_${DATE}.dump.gz"
SCRIPT

chmod +x /opt/gpu_monitor/deploy/backup_db.sh
```

**Schedule daily backups:**

```bash
echo '0 3 * * * monitoring /opt/gpu_monitor/deploy/backup_db.sh >> /opt/gpu_monitor/logs/backup.log 2>&1' | sudo tee /etc/cron.d/gpu-monitor-backup
```

> **For offsite backups:** Add `rclone copy` after the `gzip` step to upload to Backblaze B2, S3, or similar. See [rclone.org](https://rclone.org) for configuration.

### 6.5 TLS Certificate Renewal

Let's Encrypt certificates expire every 90 days. Certbot usually installs a systemd timer automatically. Verify it:

```bash
systemctl list-timers | grep certbot
# Should show: certbot.timer
```

If missing, create a renewal hook:

```bash
echo '0 4 * * 0 certbot renew --quiet --post-hook "systemctl reload nginx"' | sudo tee /etc/cron.d/certbot-renew
```

### 6.6 External Monitoring (Meta-Monitoring)

Monitor your monitoring server to catch outages:

| Probe | Tool | Frequency | Alert Threshold |
|-------|------|-----------|-----------------|
| HTTPS & TLS Cert | UptimeRobot | 60s | HTTP != 200, cert < 14 days |
| Health Endpoint | UptimeRobot JSON parser | 60s | `status != "healthy"` |
| Host Resources | Netdata / HetrixTools | 60s | CPU > 90%, Disk > 85% |

**UptimeRobot setup:**
1. Create monitors at [uptimerobot.com](https://uptimerobot.com)
2. HTTP(s) monitor for `https://monitor.example.com`
3. Keyword monitor for the health endpoint expecting `"status": "healthy"`

---

## 7. Upgrading

To deploy code updates with minimal downtime:

```bash
# SSH into the VPS
ssh root@VPS_IP

cd /opt/gpu_monitor

# Pull latest code (or transfer via rsync)
git pull origin main

# Activate venv and install any new dependencies
source venv/bin/activate
pip install -r requirements.txt 2>/dev/null || pip install django djangorestframework django-htmx psycopg2-binary argon2-cffi gunicorn requests pyyaml psutil

# Run migrations
set -a && source .env && set +a
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Fix permissions on any new template/view files
sudo chmod -R 644 /opt/gpu_monitor/templates/
sudo chmod -R 755 /opt/gpu_monitor/templates/dashboard/

# Reload Gunicorn (zero-downtime, graceful)
systemctl reload gunicorn
```

> **Migration safety rules:**
> 1. Never use `RenameField` or `DeleteModel` in a single deploy
> 2. Use additive-only evolution: add new field → dual-write → backfill → read from new → drop old
> 3. Never change column types in production tables; add new columns instead

---

## 8. Troubleshooting

### Server Issues

| Problem | Diagnosis | Fix |
|---------|-----------|-----|
| `502 Bad Gateway` from Nginx | `systemctl status gunicorn` | Check `/opt/gpu_monitor/logs/gunicorn-error.log`; usually a Python import error or DB connection failure |
| `500 Internal Server Error` | Same as above | Verify `/opt/gpu_monitor/.env` exists and has correct DB credentials |
| Database connection refused | `sudo -u postgres psql -c "SELECT 1"` | `systemctl restart postgresql`; check `/etc/postgresql/16/main/pg_hba.conf` |
|| DB migration fails | `python manage.py migrate` | Check migration order; ensure no missing dependencies |
| Certbot fails (DNS) | `dig monitor.example.com +short` | Wait for DNS propagation; ensure port 80 is open (check cloud firewall too) |
| Certbot fails (port) | `curl -I http://monitor.example.com` | Ensure port 8 open in **both** cloud firewall and UFW; certbot uses HTTP-01 challenge |
| UFW blocks SSH | Locked yourself out | Use VPS provider's console: `ufw disable`, then reconfigure |
|| `collectstatic` fails | Missing `staticfiles/` dir | `mkdir -p /opt/gpu_monitor/staticfiles && chown monitoring:monitoring /opt/gpu_monitor/staticfiles` |
|| Nginx `server_name` mismatch | `nginx -t` | Check that domain in `/etc/nginx/sites-available/gpu_monitor` matches your actual domain |
|| `PermissionError` in logs after update | New template/view file has restrictive permissions | `sudo chmod -R 644 /opt/gpu_monitor/templates/` |
|| Dashboard shows 500 after code update | Template not readable by Gunicorn | `sudo chmod 644 /opt/gpu_monitor/templates/dashboard/*.html` |

### Rig Agent Issues

| Problem | Diagnosis | Fix |
|---------|-----------|-----|
| `401 Unauthorized` in logs | API key mismatch | Regenerate key on dashboard, update `config.yaml` |
| `Connection refused` | Server firewall or Nginx issue | `curl -v https://monitor.example.com/api/v1/health/` from the rig |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Self-signed cert or DNS mismatch | Use Let's Encrypt; check server name matches cert |
|| GPU metrics empty | `pynvml` not available | `sudo /opt/monitoring-agent/venv/bin/pip install pynvml` ||
| `smartctl: command not found` | Disk tools not installed | `apt install smartmontools nvme-cli` |
| Agent hangs / overlaps | Stale lock file | `rm -f /var/lock/monitoring-agent.lock` |
| `PermissionError: config.yaml` | File owned by root | `chown monitoring-agent:monitoring-agent /etc/monitoring-agent/config.yaml` |

### Certbot Troubleshooting

```bash
# Check certbot logs
journalctl -u certbot --no-pager -n 50

# Test certificate renewal (dry run)
certbot renew --dry-run

# Manually obtain certificate
certbot --nginx -d monitor.example.com --non-interactive --agree-tos -m admin@monitor.example.com --redirect

# Check certificate expiry
openssl s_client -connect monitor.example.com:443 -servername monitor.example.com 2>/dev/null | openssl x509 -noout -dates
```

### Checking Service Status

```bash
# All critical services
systemctl status gunicorn postgresql nginx

# View recent logs
journalctl -u gunicorn --since "1 hour ago" --no-pager
journalctl -u postgresql --since "1 hour ago" --no-pager

# Gunicorn error log
tail -50 /opt/gpu_monitor/logs/gunicorn-error.log

# Gunicorn access log (HTTP requests)
tail -50 /opt/gpu_monitor/logs/gunicorn-access.log

# Django application log
tail -50 /opt/gpu_monitor/logs/app.log
```

---

## 9. File Locations Reference

### Server (`/opt/gpu_monitor/`)

| Path | Purpose |
|------|---------|
| `gpu_monitor/` | Django project (`settings.py`, `urls.py`, `wsgi.py`) |
| `accounts/` | User/auth app (models, views, API key middleware) |
| `rigs/` | Rig inventory app (models, status management command) |
| `metrics_app/` | Ingestion API (models, serializers, views) |
| `dashboard/` | HTMX dashboard (views, URL routing) |
| `dashboard/templatetags/` | Custom template filters (gpu_filters.py — gpu_model_name, gpu_model_short, time_since) |
|| `audit/` | Audit logging (models, middleware) |
|| `templates/` | Django HTML templates |
|| `deploy/` | Nginx config, Gunicorn systemd unit, install scripts, backup scripts |
| `.env` | Environment variables — mode `0600`, owned by `monitoring:monitoring` |
| `venv/` | Python virtual environment |
|| `logs/` | Application logs |
|  | `gunicorn-error.log` — Gunicorn errors, worker crashes |
|  | `gunicorn-access.log` — HTTP access log (requests, status codes) |
|  | `app.log` — Django structured JSON log |
|| `staticfiles/` | Collected static files served by Nginx |
| `manage.py` | Django management command |

### Workspace (`GPU-Rig-Monitoring-Platform/`)

| Path | Purpose |
|------|---------|
| `agent/` | Linux agent source code |
| `agent_windows/` | Windows agent source code |
| `gpu_monitor/` | Django server source code |
| `docs/` | Documentation (Architecture, Deployment, Local Deployment guides) |
| `scripts/` | Dev/test helper scripts |
| `scripts/sync_to_opt.sh` | Full workspace → /opt deployment (copy + migrate + restart) |
| `scripts/sync_agent.sh` | Agent files only → /opt deployment |
| `scripts/sync_and_migrate.sh` | Granular file sync + migrate |
| `README.md` | Project overview with directory conventions |

### Rig (`/opt/monitoring-agent/`)

| Path | Purpose |
|------|---------|
| `run.py` | Agent script |
| `venv/` | Python virtual environment |
| `/etc/monitoring-agent/config.yaml` | Agent configuration — mode `0600`, owned by `monitoring-agent:monitoring-agent` |
| `/var/log/monitoring-agent/agent.log` | Agent logs (JSON, rotated at 10 MB × 3 backups) |
| `/var/log/monitoring-agent/payload.json` | Latest full JSON payload sent to server (overwritten each run) |
| `/var/log/monitoring-agent/cron.log` | Cron output log |
| `/etc/cron.d/monitoring-agent` | Cron job definition (every 60 seconds) |
| `/etc/sudoers.d/monitoring-agent` | Sudo permissions for disk/log access |

### System

| Path | Purpose |
|------|---------|
| `/etc/nginx/sites-available/gpu_monitor` | Nginx site configuration |
| `/etc/systemd/system/gunicorn.service` | Gunicorn systemd unit |
| `/etc/cron.d/rig-status` | Rig status update cron job |
| `/etc/cron.d/gpu-monitor-backup` | Database backup cron job |
| `/etc/logrotate.d/gpu-monitor` | Log rotation configuration |
| `/var/backups/postgres/` | Database backup files |

---

## 10. Security Hardening Checklist

This section provides a comprehensive step-by-step guide for securing a fresh Ubuntu VPS before and during deployment of the GPU Rig Monitoring Platform.

---

### 10.1 Initial VPS Hardening (Before Installing Anything)

These steps should be performed immediately after provisioning a fresh VPS, before installing any application software.

#### 10.1.1 Create a Non-Root User

Never run the application as root. Create a dedicated user with sudo access:

```bash
# As root, create the deploy user
adduser deploy
usermod -aG sudo deploy

# Copy SSH key for the new user
mkdir -p /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
```

Test login: `ssh deploy@VPS_IP` before proceeding.

#### 10.1.2 Harden SSH

Edit `/etc/ssh/sshd_config`:

```bash
# As root or with sudo
sudo nano /etc/ssh/sshd_config
```

Make these changes:

```ini
# Disable root login
PermitRootLogin no

# Disable password authentication (use SSH keys only)
PasswordAuthentication no
ChallengeResponseAuthentication no

# Change default port (optional but recommended — reduces automated attacks)
# Choose a port between 1024-65535, e.g.:
Port 2222

# Restrict to specific users (replace 'deploy' with your username)
AllowUsers deploy

# Disable X1ing and TCP forwarding if not needed
X11Forwarding no
AllowTcpForwarding no

# Set idle timeout (5 minutes)
ClientAliveInterval 300
ClientAliveCountMax 2
```

Apply changes:

```bash
sudo systemctl restart sshd
```

> **WARNING:** Before restarting SSH, verify you can log in with your SSH key on the new port. Open a second terminal and test: `ssh -p 2222 deploy@VPS_IP`. If it fails, you may lock yourself out.

#### 10.1.3 Configure UFW Firewall

```bash
# Set defaults
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (use your custom port if changed above)
sudo ufw allow 2222/tcp comment 'SSH'

# Allow HTTP and HTTPS
sudo ufw allow 80/tcp comment 'HTTP'
sudo ufw allow 443/tcp comment 'HTTPS'

# Enable firewall
sudo ufw enable

# Verify
sudo ufw status verbose
```

Expected output:

```
Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
2222/tcp                   ALLOW IN    Anywhere    # SSH
80/tcp                     ALLOW IN    Anywhere    # HTTP
443/tcp                    ALLOW IN    Anywhere    # HTTPS
```

#### 10.1.4 Set Up Fail2Ban

Fail2ban blocks IPs that repeatedly fail authentication:

```bash
sudo apt install -y fail2ban

# Create local config
sudo tee /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5
backend = systemd

[sshd]
enabled = true
port = 2222
filter = sshd
maxretry = 3
bantime = 7200

# Custom jail for Django login brute-force
[django-login]
enabled = true
port = 80,443
filter = django-login
logpath = /opt/gpu_monitor/logs/gunicorn-error.log
maxretry = 10
bantime = 3600
EOF

# For custom Django filter, create:
# /etc/fail2ban/filter.d/django-login.conf
sudo tee /etc/fail2ban/filter.d/django-login.conf << 'EOF'
[Definition]
failregex = ^.*Failed login attempt from <HOST>.*$
            ^.*Authentication failed for .* from <HOST>.*$
ignoreregex =
EOF

sudo systemctl enable fail2ban
sudo systemctl start fail2ban

# Check status
sudo fail2ban-client status
```

#### 10.1.5 Enable Automatic Security Updates

```bash
sudo apt install -y unattended-upgrades apt-listchanges

sudo tee /etc/apt/apt.conf.d/50unattended-upgrades << 'EOF'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}";
    "${distro_id}:${distro_codename}-security";
    "${distro_id}ESMApps:${distro_codename}-apps-security";
    "${distro_id}ESM:${distro_codename}-infra-security";
};
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Mail "root";
EOF

sudo dpkg-reconfigure -plow unattended-upgrades
```

#### 10.1.6 Configure Time Sync

```bash
sudo apt install -y systemd-timesyncd
sudo timedatectl set-ntp true
timedatectl status
```

#### 10.1.7 Harden Kernel Parameters

```bash
sudo tee -a /etc/sysctl.d/99-security-hardening.conf << 'EOF'
# Disable IP forwarding
net.ipv4.ip_forward = 0
net.ipv6.conf.all.forwarding = 0

# Enable SYN flood protection
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = 2048
net.ipv4.tcp_synack_retries = 2

# Disable source routing
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0

# Disable ICMP redirects
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0

# Enable reverse path filtering
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# Log martian packets
net.ipv4.conf.all.log_martians = 1

# Disable IPv6 if not needed
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
EOF

sudo sysctl -p /etc/sysctl.d/99-security-hardening.conf
```

#### 10.1.8 Set Up Log Monitoring

```bash
# Install logwatch for daily log summaries
sudo apt install -y logwatch

# Create logwatch config
sudo tee /etc/logwatch/conf/logwatch.conf << 'EOF'
Output = mail
MailTo = root
Range = yesterday
Detail = Med
Service = All
Service = "-zz-network"
Service = "-zz-sys"
Service = "-eximstats"
EOF
```

---

### 10.2 Application Deployment Security

These steps are specific to the GPU Rig Monitoring Platform deployment.

#### 10.2.1 Directory Permissions

```bash
# The /opt/gpu_monitor directory should be owned by a dedicated user
# (the install script creates 'monitoring' user)
sudo chown -R monitoring:monitoring /opt/gpu_monitor
sudo chmod 750 /opt/gpu_monitor

# .env file must be readable only by the application user
sudo chmod 600 /opt/gpu_monitor/.env
sudo chown monitoring:monitoring /opt/gpu_monitor/.env

# Log directory
sudo chmod 755 /opt/gpu_monitor/logs
sudo chown monitoring:monitoring /opt/gpu_monitor/logs

# Agent config on rigs
sudo chmod 600 /etc/monitoring-agent/config.yaml
sudo chown monitoring-agent:monitoring-agent /etc/monitoring-agent/config.yaml
```

#### 10.2.2 PostgreSQL Security

By default, PostgreSQL listens on localhost only. Verify this:

```bash
# Check listen address
sudo grep "listen_addresses" /etc/postgresql/*/main/postgresql.conf
# Should show: listen_addresses = 'localhost'

# Check pg_hba.conf for auth method
sudo grep -v "^#" /etc/postgresql/*/main/pg_hba.conf | grep -v "^$"
```

Expected:

```
local   all             postgres                                peer
local   all             all                                     scram-sha-256
host    all             all             127.0.0.1/32            scram-sha-256
host    all             all             ::1/128                 scram-sha-256
```

If `pg_hba.conf` has `trust` or `md5` auth, change to `scram-sha-256`:

```bash
sudo sed -i 's/md5/scram-sha-256/g' /etc/postgresql/*/main/pg_hba.conf
sudo sed -i 's/trust/scram-sha-256/g' /etc/postgresql/*/main/pg_hba.conf
sudo systemctl restart postgresql
```

Create a dedicated database user with limited privileges:

```bash
sudo -u postgres psql << 'EOF'
-- Create application user (install script does this automatically)
CREATE USER gpu_monitor WITH PASSWORD 'strong_random_password_here';
CREATE DATABASE gpu_monitor OWNER gpu_monitor;
GRANT ALL PRIVILEGES ON DATABASE gpu_monitor TO gpu_monitor;

-- Revoke public access
REVOKE ALL ON DATABASE gpu_monitor FROM PUBLIC;
EOF
```

#### 10.2.3 Nginx Security Headers

The Nginx config should include security headers. Edit `/etc/nginx/sites-available/gpu_monitor`:

```nginx
server {
    listen 443 ssl http2;
    server_name monitor.example.com;

    ssl_certificate /etc/letsencrypt/live/monitor.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/monitor.example.com/privkey.pem;

    # TLS hardening
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' cdn.jsdelivr.net; img-src 'self' data:;" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

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

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name monitor.example.com;
    return 301 https://$server_name$request_uri;
}
```

Test and reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

#### 10.2.4 Lock Down User Accounts

Both the `monitoring` (server) and `monitoring-agent` (rig) users should have nologin shells:

```bash
# Verify
grep -E "monitoring|monitoring-agent" /etc/passwd
```

Expected output:

```
monitoring:x:1001:1001:,,,:/home/monitoring:/usr/sbin/nologin
monitoring-agent:x:1002:1002:,,,:/usr/sbin/nologin
```

If `/usr/sbin/nologin` is not available:

```bash
sudo apt install -y util-linux
sudo usermod -s /usr/sbin/nologin monitoring
sudo usermod -s /usr/sbin/nologin monitoring-agent
```

#### 10.2.5 Verify Django Security Settings

In `/opt/gpu_monitor/.env`:

```bash
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=monitor.example.com
CSRF_TRUSTED_ORIGINS=["https://monitor.example.com"]
SECTURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

Verify the secret key is random:

```bash
grep DJANGO_SECRET_KEY /opt/gpu_monitor/.env
# Should be a 50+ character random string, NOT "change-me-..."
```

Generate a new key if needed:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

---

### 10.3 Post-Deployment Verification

Run these checks after completing deployment:

```bash
# 1. Verify UFW status
sudo ufw status verbose

# 2. Verify Fail2ban is running
sudo fail2ban-client status

# 3. Verify unattended-upgrades is enabled
sudo systemctl status unattended-upgrades

# 4. Verify no root SSH login
grep PermitRootLogin /etc/ssh/sshd_config
# Should show: PermitRootLogin no

# 5. Verify TLS rating
# Visit: https://www.ssllabs.com/ssltest/analyze.html?d=monitor.example.com
# Expected: A or A+

# 6. Verify security headers
curl -I https://monitor.example.com
# Should include: Strict-Transport-Security, X-Frame-Options, etc.

# 7. Verify PostgreSQL is localhost-only
sudo grep "listen_addresses" /etc/postgresql/*/main/postgresql.conf
# Should show: listen_addresses = 'localhost'

# 8. Verify no unnecessary services listening
sudo ss -tlnp
# Expected: 2222 (SSH), 80 (Nginx), 443 (Nginx), 5432 (PostgreSQL localhost only)

# 9. Verify file permissions
ls -la /opt/gpu_monitor/.env
# Should be: -rw------- monitoring monitoring

# 10. Check for open ports from outside
# From another machine:
nmap -p 1-65535 YOUR_VPS_IP
# Should show only: 2222, 80, 443 (or just 443 if SSH on non-standard port)
```

---

### 10.4 Ongoing Maintenance

| Task | Frequency | Command/Action |
|---|---|---|
| Review Fail2ban bans | Weekly | `sudo fail2ban-client status sshd` |
| Check security updates | Daily (automatic) | `cat /var/log/unattended-upgrades/unattended-upgrades.log` |
| Review auth logs | Weekly | `sudo journalctl -u sshd --since "7 days ago" \| grep Failed` |
| Rotate database backups | Daily (automatic) | Verify `/var/backups/postgres/` has recent files |
| TLS certificate check | Monthly | `certbot certificates` |
| Full security audit | Quarterly | `lynis audit system` (install lynis first) |
| Update OS packages | Monthly | `sudo apt update && sudo apt upgrade` |
| Check disk space | Daily | `df -h` and monitor via dashboard |
| Review PostgreSQL logs | Weekly | Check `/var/log/postgresql/` for anomalies |

---

### 10.5 Quick Security Checklist Summary

- [ ] Non-root deploy user created with SSH key access
- [ ] Root SSH login disabled
- [ ] Password SSH authentication disabled
- [ ] SSH port changed from 22 (optional)
- [ ] UFW firewall enabled (only 2222/80/443 open)
- [ ] Fail2ban installed and configured
- [ ] Automatic security updates enabled
- [ ] PostgreSQL listens on localhost only with scram-sha-256 auth
- [ ] TLS 1.2+ with strong cipher suites (A+ on SSL Labs)
- [ ] Security headers configured (HSTS, CSP, X-Frame-Options)
- [ ] `DJANGO_DEBUG=False`
- [ ] Strong random `DJANGO_SECRET_KEY`
- [ ] `.env` file mode 600
- [ ] `monitoring` and `monitoring-agent` users have nologin shells
- [ ] Agent sudoers limited to read-only commands only
- [ ] Nginx redirects HTTP to HTTPS
- [ ] No unnecessary services running

---

## Next Steps

1. **Enumerate your rigs:** Deploy agents to all GPU rigs you want to monitor
2. **Set up tagging:** Organize rigs with tags (e.g., `farm-01`, `a100-gpus`, `production`)
3. **Configure alerts:** Add UptimeRobot monitoring for the dashboard itself
4. **Set up backups:** Configure offsite backups with rclone
5. **Review audit logs:** Check `/opt/gpu_monitor/logs/app.log` for security events

---

## Deploying on a Local VM (No Domain)

For local testing without a domain name, see `LOCAL_DEPLOYMENT_GUIDE.md`.
