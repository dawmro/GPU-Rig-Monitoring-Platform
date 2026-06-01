# GPU Rig Monitoring Platform — Deployment Guide

**Version:** 1.0
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
   - 4.7 [Set Up TimescaleDB Hypertables](#47-set-up-timescaledb-hypertables)
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
   - 6.2 [Log Rotation](#62-log-rotation)
   - 6.3 [Database Backups](#63-database-backups)
   - 6.4 [TLS Certificate Renewal](#64-tls-certificate-renewal)
   - 6.5 [External Monitoring](#65-external-monitoring)
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
│  │ Django + DRF    │ ────────────→  │ PostgreSQL + TimescaleDB     │    │
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
| Storage | 500 GB+ NVMe SSD (NVMe required for TimescaleDB) |
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

> **Why NVMe is mandatory:** TimescaleDB performs chunk compression and continuous aggregate refreshes in the background. SATA SSDs will bottleneck during these operations, causing ingestion lag.

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

**Option B — Clone from Git:**

```bash
# On the VPS:
git clone https://github.com/yourorg/gpu_monitor.git /tmp/gpu_monitor
```

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
| 1 | Installs system packages (Python, PostgreSQL, TimescaleDB, Nginx, certbot, UFW) |
| 2 | Runs `timescaledb-tune` to optimize `postgresql.conf` |
| 3 | Creates `gpu_monitor` DB user and database, enables TimescaleDB extension |
| 4 | Creates `monitoring` OS user (no-login shell) |
| 5 | Sets up Python virtualenv and installs dependencies |
| 6 | Writes `/opt/gpu_monitor/.env` with secrets and DB credentials |
| 7 | Runs Django migrations + `collectstatic` |
| 8 | Installs Gunicorn systemd unit and starts it |
| 9 | Installs Nginx site config, removes default site, restarts Nginx |
| 10 | Runs Certbot to obtain Let's Encrypt TLS certificate |
| 11 | Configures UFW firewall (allow 22/80/443) |
| 12 | Enables and starts all services |

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
```

### 4.6 Create an Admin User

The script cannot create a superuser automatically (interactive prompts). Run:

```bash
sudo -u monitoring bash -c 'cd /opt/gpu_monitor && source venv/bin/activate && set -a && source .env && set +a && python manage.py createsuperuser'
```

Enter email, username, and password when prompted. Use the email to log into the dashboard.

### 4.7 Set Up TimescaleDB Hypertables

This step converts the raw PostgreSQL tables into TimescaleDB hypertables and sets up retention policies and continuous aggregates for efficient time-series queries.

```bash
sudo -u monitoring bash -c 'cd /opt/gpu_monitor && source venv/bin/activate && set -a && source .env && set +a && python manage.py setup_timescale'
```

**Expected output:**

```
Created hypertable: metrics_metricsnapshot
Added 7-day retention policy
Created hourly continuous aggregate
Added hourly refresh policy
TimescaleDB setup complete
```

### 4.8 Verify the Deployment

```bash
# All services active?
systemctl is-active gunicorn postgresql nginx

# Database responding?
sudo -u postgres psql -d gpu_monitor -c "SELECT 1"

# TimescaleDB extension loaded?
sudo -u postgres psql -d gpu_monitor -c "\dx" | grep timescaledb

# Hypertable configured?
sudo -u postgres psql -d gpu_monitor -c "SELECT * FROM timescaledb_information.hypertables;"

# Health endpoint returns healthy?
curl -s https://monitor.example.com/api/v1/health/ | python3 -m json.tool
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
https://monitor.example.com/accounts/login/
```

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

The platform needs a periodic task to mark rigs as **Stale** (not seen in 2–10 minutes) or **Offline** (not seen in 10+ minutes). Set up a cron job:

```bash
echo '*/2 * * * * monitoring /opt/gpu_monitor/venv/bin/python /opt/gpu_monitor/manage.py update_rig_status >> /opt/gpu_monitor/logs/rig_status.log 2>&1' | sudo tee /etc/cron.d/rig-status
```

This runs every 2 minutes as recommended by the architecture specification.

### 6.2 Log Rotation

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

### 6.3 Database Backups

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

### 6.4 TLS Certificate Renewal

Let's Encrypt certificates expire every 90 days. Certbot usually installs a systemd timer automatically. Verify it:

```bash
systemctl list-timers | grep certbot
# Should show: certbot.timer
```

If missing, create a renewal hook:

```bash
echo '0 4 * * 0 certbot renew --quiet --post-hook "systemctl reload nginx"' | sudo tee /etc/cron.d/certbot-renew
```

### 6.5 External Monitoring (Meta-Monitoring)

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

# Reload Gunicorn (zero-downtime, graceful)
systemctl reload gunicorn
```

> **Migration safety rules:**
> 1. Never use `RenameField` or `DeleteModel` in a single deploy
> 2. Use additive-only evolution: add new field → dual-write → backfill → read from new → drop old
> 3. Never change column types in TimescaleDB hypertables; add new columns instead

---

## 8. Troubleshooting

### Server Issues

| Problem | Diagnosis | Fix |
|---------|-----------|-----|
| `502 Bad Gateway` from Nginx | `systemctl status gunicorn` | Check `/opt/gpu_monitor/logs/gunicorn-error.log`; usually a Python import error or DB connection failure |
| `500 Internal Server Error` | Same as above | Verify `/opt/gpu_monitor/.env` exists and has correct DB credentials |
| Database connection refused | `sudo -u postgres psql -c "SELECT 1"` | `systemctl restart postgresql`; check `/etc/postgresql/16/main/pg_hba.conf` |
| `timescaledb.control` not found | `sudo -u postgres psql -c "\dx"` | Reinstall TimescaleDB: `sudo apt install --reinstall timescaledb-2-postgresql-16` then `sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"` |
| Certbot fails (DNS) | `dig monitor.example.com +short` | Wait for DNS propagation; ensure port 80 is open (check cloud firewall too) |
| Certbot fails (port) | `curl -I http://monitor.example.com` | Ensure port 8 open in **both** cloud firewall and UFW; certbot uses HTTP-01 challenge |
| UFW blocks SSH | Locked yourself out | Use VPS provider's console: `ufw disable`, then reconfigure |
| `collectstatic` fails | Missing `staticfiles/` dir | `mkdir -p /opt/gpu_monitor/staticfiles && chown monitoring:monitoring /opt/gpu_monitor/staticfiles` |
| Nginx `server_name` mismatch | `nginx -t` | Check that domain in `/etc/nginx/sites-available/gpu_monitor` matches your actual domain |

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
```

---

## 9. File Locations Reference

### Server (`/opt/gpu_monitor/`)

| Path | Purpose |
|------|---------|
| `gpu_monitor/` | Django project (`settings.py`, `urls.py`, `wsgi.py`) |
| `accounts/` | User/auth app (models, views, API key middleware) |
| `rigs/` | Rig inventory app (models, status management command) |
| `metrics_app/` | Ingestion API (models, serializers, TimescaleDB setup command) |
| `dashboard/` | HTMX dashboard (views, URL routing) |
| `audit/` | Audit logging (models, middleware) |
| `templates/` | Django HTML templates |
| `deploy/` | Nginx config, Gunicorn systemd unit, install scripts, backup scripts |
| `.env` | Environment variables — mode `0600`, owned by `monitoring:monitoring` |
| `venv/` | Python virtual environment |
| `logs/` | Application logs |
| `staticfiles/` | Collected static files served by Nginx |
| `manage.py` | Django management command |

### Rig (`/opt/monitoring-agent/`)

| Path | Purpose |
|------|---------|
| `run.py` | Agent script |
| `venv/` | Python virtual environment |
| `/etc/monitoring-agent/config.yaml` | Agent configuration — mode `0600`, owned by `monitoring-agent:monitoring-agent` |
| `/var/log/monitoring-agent/agent.log` | Agent logs (JSON, rotated at 10 MB × 3 backups) |
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

- [ ] SSH: Disable password auth, use key-only (`/etc/ssh/sshd_config`: `PasswordAuthentication no`)
- [ ] SSH: Change default port (optional, reduces noise)
- [ ] UFW: Only ports 22, 80, 443 open (already configured by install script)
- [ ] TLS: Verify with [SSL Labs](https://www.ssllabs.com/ssltest/) — should rate A or A+
- [ ] `DJANGO_DEBUG=False` in `.env` (already set by install script)
- [ ] `DJANGO_SECRET_KEY` is random (generated by install script)
- [ ] Database password is strong (generated by install script)
- [ ] `.env` file is mode `0600`
- [ ] Agent config is mode `0600`
- [ ] `monitoring` user has nologin shell
- [ ] `monitoring-agent` user has nologin shell
- [ ] PostgreSQL listens on localhost only (`pg_hba.conf`)
- [ ] Regular security updates: `unattended-upgrades` configured
- [ ] Fail2ban installed for SSH brute-force protection

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
