# GPU Rig Monitoring Platform — Deployment Guide

**Version:** 1.0  
**Target OS:** Ubuntu 22.04 / 24.04 LTS (single VPS)  
**Last Updated:** 2026-05-31

---

## Table of Contents

1. [Prerequisites & Planning](#1-prerequisites--planning)
2. [Domain Name & DNS Setup](#2-domain-name--dns-setup)
3. [Server Deployment (Step-by-Step)](#3-server-deployment-step-by-step)
4. [TimescaleDB Hypertable Setup](#4-timescaledb-hypertable-setup)
5. [Rig Agent Deployment (Step-by-Step)](#5-rig-agent-deployment-step-by-step)
6. [Post-Deployment Verification](#6-post-deployment-verification)
7. [Troubleshooting](#7-troubleshooting)
8. [File Locations Reference](#8-file-locations-reference)

---

## 1. Prerequisites & Planning

### 1.1 VPS Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| **OS** | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| **vCPU** | 4 | 4–8 |
| **RAM** | 16 GB | 16–32 GB |
| **Storage** | 250 GB NVMe SSD | 500 GB NVMe SSD |
| **Network** | 1 public IPv4 | 1 public IPv4, 1 Gbps uplink |
| **Database** | PostgreSQL 15+ with TimescaleDB 2.x | PostgreSQL 16 + TimescaleDB 2.14+ |

> **Why NVMe is mandatory:** TimescaleDB performs chunk compression and continuous aggregate refreshes in the background. SATA SSDs will bottleneck during these operations, causing ingestion lag.

### 1.2 VPS Providers (Examples)

- **Hetzner** (cost-effective, EU): CX41 (4 vCPU, 16 GB) ~€15/mo
- **DigitalOcean** (managed, global): Basic Droplet (4 GB RAM+) ~$24/mo
- **AWS EC2**: t3.xlarge (4 vCPU, 16 GB) ~$120/mo
- **Linode**: Dedicated 8 GB ~$60/mo

### 1.3 What You Need Before Starting

- [ ] A VPS provisioned with Ubuntu 22.04 or 24.04, root SSH access
- [ ] A registered domain name (e.g., `example.com`)
- [ ] DNS A record pointing `monitor.example.com` → your VPS IP
- [ ] An SSH key pair for secure access
- [ ] The project files transferred to the VPS (via `git clone` or `rsync`)

---

## 2. Domain Name & DNS Setup

### 2.1 Getting a Domain Name

If you don't already have a domain, register one from any registrar:

| Registrar | Approximate Cost (.com) |
|---|---|
| Namecheap | ~$10/year |
| Cloudflare Registrar | ~$10/year (at cost) |
| Google Domains | ~$12/year |
| GoDaddy | ~$15/year |

For a monitoring dashboard, consider:
- A subdomain of your existing domain: `monitor.yourcompany.com`
- A cheap dedicated domain: `yourfleet.io`

### 2.2 Configuring DNS

Once you have a domain, create an **A record** pointing to your VPS public IP:

```
Type: A
Name: monitor
Value: 203.0.113.50      ← your VPS public IPv4
TTL: 300 (5 minutes)
```

Example using Cloudflare DNS dashboard:
1. Log in to Cloudflare → select your domain
2. Go to **DNS** → **Records**
3. Click **Add Record**
4. Set Type = `A`, Name = `monitor`, IPv4 = your VPS IP
5. Save

**Verify DNS propagation:**
```bash
# From any machine:
dig monitor.example.com +short
# Should return: 203.0.113.50 (your VPS IP)
```

> **Tip:** Set TTL to 300 seconds (5 min) during setup so changes propagate quickly. You can increase it later.

### 2.3 Firewall Prerequisites

Before running the install script, ensure your VPS cloud firewall (if any) allows:
- **Inbound:** TCP 22 (SSH), TCP 80 (HTTP), TCP 443 (HTTPS)
- **Outbound:** all (for package downloads, Let's Encrypt, etc.)

If your provider has no cloud firewall, the script configures UFW locally.

---

## 3. Server Deployment (Step-by-Step)

### 3.1 Transfer Project Files to the VPS

**Option A: Using rsync (recommended)**
```bash
# From your local machine or build machine:
rsync -avz --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
    /home/qrv/workspace/gpu_monitor/ root@VPS_IP:/tmp/gpu_monitor/
```

**Option B: Using git**
```bash
# On the VPS:
git clone https://github.com/yourorg/gpu_monitor.git /tmp/gpu_monitor
```

**Option C: Using scp**
```bash
# From your machine:
scp -r /home/qrv/workspace/gpu_monitor root@VPS_IP:/tmp/gpu_monitor
```

### 3.2 SSH into the VPS

```bash
ssh root@VPS_IP
```

### 3.3 Run the Deployment Script

```bash
# Move project into place
mv /tmp/gpu_monitor /opt/gpu_monitor

# Make the script executable
chmod +x /opt/gpu_monitor/deploy/server_install.sh

# Run it — pass your domain as the only argument
/opt/gpu_monitor/deploy/server_install.sh monitor.example.com
```

**The script performs these operations, in order:**

| Step | What It Does | Output |
|---|---|---|
| 1 | Installs system packages (Python, PostgreSQL, TimescaleDB, Nginx, certbot, UFW) | `apt install` output |
| 2 | Runs `timescaledb-tune` to optimize `postgresql.conf` | Tuning log |
| 3 | Creates `gpu_monitor` DB user and database, enables TimescaleDB extension | DB password printed |
| 4 | Creates `monitoring` OS user (no-login shell) | `Created user: monitoring` |
| 5 | Sets up Python virtualenv at `/opt/gpu_monitor/venv` | `pip install` output |
| 6 | Writes `/opt/gpu_monitor/.env` with secrets and DB credentials | Silently created (mode 600) |
| 7 | Runs Django migrations + collectstatic | Migration output |
| 8 | Installs Gunicorn systemd unit and starts it | `systemctl enable/start` |
| 9 | Installs Nginx site config, removes default site, restarts Nginx | `nginx -t` output |
| 10 | Runs Certbot to obtain Let's Encrypt TLS certificate | Certbot output |
| 11 | Configures UFW firewall (allow 22/80/443) | `ufw enable` output |
| 12 | Enables and starts all services | `systemctl enable` output |

### 3.4 Save the Database Password

The script prints the auto-generated database password. **Save it somewhere safe** (password manager). It is also saved to `/opt/gpu_monitor/.env`:

```bash
cat /opt/gpu_monitor/.env
```

Expected contents:
```bash
DJANGO_SECRET_KEY=random-secret-key-here
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=monitor.example.com
DB_NAME=gpu_monitor
DB_USER=gpu_monitor
DB_PASSWORD=your-random-password-here
DB_HOST=127.0.0.1
DB_PORT=5432
```

### 3.5 Create an Admin User

The script cannot create a superuser automatically (interactive prompts). Run:

```bash
sudo -u monitoring bash -c 'cd /opt/gpu_monitor && source venv/bin/activate && set -a && source .env && set +a && python manage.py createsuperuser'
```

You'll be prompted for:
- **Email address:** (e.g., `admin@example.com`)
- **Username:** (e.g., `admin`)
- **Password:** (choose a strong password)
- **Password (again):** (confirm)

### 3.6 Verify the Server is Running

```bash
# Check Gunicorn
systemctl status gunicorn

# Check Nginx
systemctl status nginx

# Check PostgreSQL
systemctl status postgresql

# Test the health endpoint
curl -s http://127.0.0.1:8000/api/v1/health/ | python3 -m json.tool
```

Expected response:
```json
{
    "status": "healthy",
    "version": "1.0.0",
    "uptime_s": 0,
    "db_connection": "ok",
    "active_rigs": 0
}
```

### 3.7 Log In to the Dashboard

Open your browser and navigate to:
```
https://monitor.example.com/accounts/login/
```

Log in with the admin credentials you created in Step 3.5.

---

## 4. TimescaleDB Hypertable Setup

This step converts the raw PostgreSQL tables into TimescaleDB hypertables and sets up retention policies and continuous aggregates for efficient time-series queries.

### 4.1 Run the Management Command

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

### 4.2 What This Does

| Operation | Purpose |
|---|---|
| `create_hypertable('metrics_metricsnapshot', 'timestamp')` | Converts the raw metrics table into a TimescaleDB hypertable, partitioned into 1-day chunks for fast writes and queries |
| `add_retention_policy(..., drop_after => '7 days')` | Automatically drops raw metric data older than 7 days to manage disk space |
| `CREATE MATERIALIZED VIEW metrics_hourly_agg` | Creates a continuous aggregate that pre-computes hourly rollups (avg/max CPU, temp, memory) |
| `add_continuous_aggregate_policy(...)` | Refreshes the hourly aggregate every hour with a 1-hour offset for real-time completeness |

### 4.3 Verify TimescaleDB Extension

```bash
sudo -u postgres psql -d gpu_monitor -c "\dx"
```

You should see `timescaledb` in the extensions list.

---

## 5. Rig Agent Deployment (Step-by-Step)

Deploy the agent on **each GPU rig** you want to monitor.

### 5.1 Prerequisites for Each Rig

| Requirement | Details |
|---|---|
| **OS** | Linux (Ubuntu 20.04+, Debian 11+, or similar) |
| **Python** | 3.10+ (3.8 works but 3.10+ recommended) |
| **Network** | HTTPS access to `https://monitor.example.com` |
| **Privileges** | Root/sudo access for installation |
| **NVIDIA GPUs** | `nvidia-smi` must be available for GPU monitoring |

### 5.2 Transfer Agent Files to the Rig

From your local machine or the server:

```bash
rsync -avz /home/qrv/workspace/agent/ root@RIG_IP:/tmp/agent/
```

### 5.3 Get an API Key from the Dashboard

1. Log in to `https://monitor.example.com/accounts/login/`
2. Click **API Keys** in the top navigation bar
3. Enter a descriptive name (e.g., `rig-farm-01-node-3`) and click **Create Key**
4. **Copy the displayed API key immediately** — it is shown only once
5. Keep this key ready for the next step

### 5.4 Run the Agent Install Script on the Rig

```bash
ssh root@RIG_IP

# Create install directory and copy files
mkdir -p /opt/monitoring-agent
cp /tmp/agent/run.py /opt/monitoring-agent/run.py
cp /tmp/agent/config.yaml.example /tmp/agent/config.yaml

# Run the installer
chmod +x /tmp/agent/install.sh
/tmp/agent/install.sh
```

**The script performs these operations:**

| Step | What It Does |
|---|---|
| 1 | Creates `monitoring-agent` system user (no-login shell) |
| 2 | Creates directories: `/opt/monitoring-agent/`, `/etc/monitoring-agent/`, `/var/log/monitoring-agent/` |
| 3 | Creates Python virtualenv at `/opt/monitoring-agent/venv` |
| 4 | Installs Python dependencies: `psutil`, `py-cpuinfo`, `requests`, `pyyaml`, `docker`, `nvidia-ml-py3` |
| 5 | Copies `run.py` and creates config template at `/etc/monitoring-agent/config.yaml` |
| 6 | Configures sudoers for SMART disk queries (`smartctl`, `nvme`, `journalctl`) |
| 7 | Creates cron job at `/etc/cron.d/monitoring-agent` (every 60 seconds, with `flock` to prevent overlaps) |

### 5.5 Configure the Agent

Edit the config file on the rig:

```bash
nano /etc/monitoring-agent/config.yaml
```

Set these values:

```yaml
rig_uuid: "auto"
api_key: "PASTE_YOUR_API_KEY_HERE"
server_endpoint: "https://monitor.example.com"
expected_gpu_count: 0
collection_timeout_s: 45
retry_attempts: 3
debug_mode: false
```

**Field explanations:**

| Field | Description |
|---|---|
| `rig_uuid` | Set to `"auto"` to generate a UUID on first run. After the first run, check the file again — it will be replaced with the actual UUID. This UUID is permanent. |
| `api_key` | The exact key copied from the server dashboard. No quotes needed unless the key contains special characters. |
| `server_endpoint` | Your server's HTTPS URL **without** a trailing slash. |
| `expected_gpu_count` | Set to `0` for auto-detect. Set to your actual GPU count (e.g., `4`) if you want the server to flag a mismatch. |
| `collection_timeout_s` | Hard limit for metric collection + upload. Default 45s leaves 15s buffer within the 60s cron cycle. |
| `retry_attempts` | Number of retries on transient failures. Default 3 with exponential backoff (1s → 2s → 4s). |
| `debug_mode` | Set to `true` for verbose logging and disabled gzip. Use only for troubleshooting. |

### 5.6 Test the Agent Manually

```bash
sudo -u monitoring-agent /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/run.py
```

Check the output. On success, you'll see structured JSON logs:

```json
{"ts":"2024-05-20T14:32:00","level":"INFO","module":"main","msg":"Starting collection for rig a1b2c3d4-..."}
{"ts":"2024-05-20T14:32:02","level":"INFO","module":"transport","msg":"Ingest response: 200 {\"status\": \"new\"}"}
{"ts":"2024-05-20T14:32:02","level":"INFO","module":"main","msg":"Payload accepted: new"}
```

### 5.7 Verify on the Dashboard

1. Open `https://monitor.example.com/dashboard/rigs/`
2. Your rig should appear within **2 minutes** (1 minute for cron to trigger + collection time)
3. The status badge shows **● Online** (green) once the first payload is received
4. Click the rig name to see the **detail page** with live metrics (CPU, GPU, memory, Docker, storage, errors) — all refreshing every 30 seconds via HTMX

---

## 6. Post-Deployment Verification

### 6.1 Server Health Checks

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

### 6.2 Agent Health Checks

```bash
# Agent runs without errors?
tail -20 /var/log/monitoring-agent/agent.log

# Cron job is set up?
cat /etc/cron.d/monitoring-agent

# Config is valid YAML?
python3 -c "import yaml; yaml.safe_load(open('/etc/monitoring-agent/config.yaml'))" && echo "OK"
```

### 6.3 End-to-End Verification

| Check | How | Expected Result |
|---|---|---|
| Rig appears on dashboard | Open fleet page within 2 minutes | Rig row visible with Online badge |
| Metrics are live | Open rig detail page | CPU %, GPU %, memory values displayed |
| HTMX polling works | Wait 30 seconds on detail page | Metrics update without page reload |
| TLS certificate valid | Open `https://monitor.example.com` | Browser shows padlock, no warnings |
| API rejects bad keys | `curl -H "X-API-Key: wrong" /api/v1/ingest/` | Returns 401 |

---

## 7. Troubleshooting

### Server Issues

| Problem | Diagnosis | Fix |
|---|---|---|
| `502 Bad Gateway` from Nginx | `systemctl status gunicorn` | Check `/opt/gpu_monitor/logs/gunicorn-error.log`; usually a Python import error or DB connection failure |
| `500 Internal Server Error` | Same as above | Check that `/opt/gpu_monitor/.env` exists and has correct DB credentials |
| Database connection refused | `sudo -u postgres psql -c "SELECT 1"` | `systemctl restart postgresql`; check `pg_hba.conf` |
| `timescaledb.control` not found | Extension not installed | Re-run `setup_timescalescope` or verify with `\dx` |
| Certbot fails | DNS not pointing to VPS | Verify with `dig monitor.example.com +short`; ensure port 80 is open |
| UFW blocks SSH | Locked yourself out | Use VPS provider's console to disable UFW: `ufw disable` |

### Rig Agent Issues

| Problem | Diagnosis | Fix |
|---|---|---|
| `401 Unauthorized` in logs | API key mismatch | Regenerate key on dashboard, update `config.yaml` |
| `Connection refused` | Server firewall or Nginx issue | `curl -v https://monitor.example.com/api/v1/health/` from the rig |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Self-signed cert or DNS mismatch | Use Let's Encrypt; check server name matches cert |
| GPU metrics empty | `pynvml` not available | `sudo /opt/monitoring-agent/venv/bin/pip install nvidia-ml-py3` |
| `smartctl: command not found` | Disk tools not installed | `apt install smartmontools nvme-cli` |
| Agent hangs / overlaps | Stale lock file | `rm -f /var/lock/monitoring-agent.lock` |

---

## 8. File Locations Reference

### Server (`/opt/gpu_monitor/`)

| Path | Purpose |
|---|---|
| `gpu_monitor/` | Django project (`settings.py`, `urls.py`, `wsgi.py`) |
| `accounts/` | User/auth app (models, views, API key middleware) |
| `rigs/` | Rig inventory app (models, status management command) |
| `metrics_app/` | Ingestion API (models, serializers, views, TimescaleDB setup command) |
| `dashboard/` | HTMX dashboard (views, URL routing) |
| `audit/` | Audit logging (models, middleware) |
| `templates/` | Django HTML templates (`base.html`, `login.html`, dashboard templates) |
| `deploy/` | Nginx config, Gunicorn systemd unit, install scripts, backup scripts |
| `.env` | Environment variables (secret key, DB credentials) — mode `600` |
| `venv/` | Python virtual environment |
| `logs/` | Application logs (`gunicorn-error.log`, `gunicorn-access.log`, `app.log`) |
| `staticfiles/` | Collected static files served by Nginx |

### Rig (`/opt/monitoring-agent/`)

| Path | Purpose |
|---|---|
| `run.py` | Agent script (metric collection, payload construction, HTTPS upload) |
| `venv/` | Python virtual environment |
| `/etc/monitoring-agent/config.yaml` | Agent configuration (API key, server URL, UUID) |
| `/var/log/monitoring-agent/agent.log` | Agent logs (JSON, rotated at 10 MB × 3 backups) |
| `/etc/cron.d/monitoring-agent` | Cron job definition (every 60 seconds) |
| `/etc/sudoers.d/monitoring-agent` | Sudo permissions for disk/log access |

---

## Next Steps

1. **Create admin user** (Section 3.5)
2. **Set up TimescaleDB hypertables** (Section 4)
3. **Deploy agents to your rigs** (Section 5)
4. **Verify everything works** (Section 6)

Once deployed, the dashboard will be accessible at `https://monitor.example.com/` and will show real-time telemetry from all enrolled rigs.
