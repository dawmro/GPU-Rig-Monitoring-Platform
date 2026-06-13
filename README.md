# GPU Rig Monitoring Platform

A single-server telemetry dashboard for GPU rigs running AI/LLM workloads. Collects hardware metrics from remote agents and displays them in a live-updating web dashboard.

## Quick Links

| What | Where |
|------|-------|
| **Production deployment** | [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) |
| **Local testing** | [`docs/LOCAL_DEPLOYMENT_GUIDE.md`](docs/LOCAL_DEPLOYMENT_GUIDE.md) |
| **Architecture reference** | [`docs/GPU_Rig_Monitoring_Architecture.md`](docs/GPU_Rig_Monitoring_Architecture.md) |
| **Linux agent** | [`agent/README.md`](agent/README.md) |
| **Windows agent** | [`agent_windows/README.md`](agent_windows/README.md) |

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        RIG FLEET (Untrusted)                            │
│                                                                         │
│  ┌─────────────────┐    HTTPS POST /api/v1/ingest/    ┌──────────────┐  │
│  │ Python Agent    │ ────────────────────────────────→ │   Nginx      │  │
│  │ (cron 60s)      │                                   │   Reverse    │  │
│  │ Linux + Windows │                                   │   Proxy      │  │
│  └─────────────────┘                                   └──────┬───────┘  │
└───────────────────────────────────────────────────────────────┼──────────┘
                                                                │ HTTPS
                                                                │ (TLS 1.3)
┌───────────────────────────────────────────────────────────────┼──────────┐
│                   SINGLE UBUNTU VPS (Trusted)                 │          │
│                                                               ▼          │
│  ┌─────────────────┐    TCP/5432    ┌──────────────────────────────┐    │
│  │ Django + DRF    │ ────────────→ │ PostgreSQL + TimescaleDB     │    │
│  │ (Gunicorn)      │                │ (hypertables for metrics)    │    │
│  └────────┬────────┘                └──────────────────────────────┘    │
│           │                                                             │
│           │ Render/Query                                                 │
│           ▼                                                             │
│  ┌─────────────────┐    HTTPS GET/POST    ┌──────────────────────────┐  │
│  │ HTMX Dashboard  │ ←─────────────────── │   User Browser           │  │
│  │ (30s polling)   │    + HTMX Polling     │                          │  │
│  └─────────────────┘                       └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

**Server:** Django 6.x + DRF, PostgreSQL 16 + TimescaleDB, Gunicorn, Nginx, HTMX  
**Agents:** Linux (cron) and Windows (Task Scheduler), Python 3.10+, psutil + pynvml

## Repository Structure

```
GPU-Rig-Monitoring-Platform/
├── agent/                     # Linux monitoring agent
│   ├── README.md              # Agent documentation
│   ├── run.py                 # Agent script (psutil + pynvml collectors)
│   ├── check_update.py        # Auto-update checker
│   ├── config.yaml.example    # Config template → /etc/monitoring-agent/config.yaml
│   └── install.sh             # Installer (creates user, venv, cron, sudoers)
│
├── agent_windows/             # Windows monitoring agent
│   ├── README.md              # Agent documentation
│   ├── run.py                 # Agent script (WMI + psutil collectors)
│   ├── check_update.py        # Auto-update checker
│   └── config.yaml.example    # Config template → ./config.yaml
│
├── gpu_monitor/               # Django project (server side)
│   ├── gpu_monitor/           # Django settings package (settings, urls, wsgi)
│   ├── accounts/              # Auth app (email login, API key management)
│   ├── rigs/                  # Rig inventory (Rig model, status state machine)
│   ├── metrics_app/           # GPU metrics data layer (ingestion API, models)
│   ├── dashboard/             # HTMX dashboard (views, templates, tab tags)
│   ├── audit/                 # Audit logging (models, middleware)
│   │
│   ├── deploy/                # PRODUCTION deployment artifacts
│   │   ├── nginx.conf         # Nginx site config (TLS 1.3, rate limiting)
│   │   ├── gunicorn.service   # Gunicorn systemd unit
│   │   ├── server_install.sh  # Full production server installer
│   │   ├── data_retention.sh  # Daily compaction + cleanup cron wrapper
│   │   └── update_rig_status.sh  # 2-minute rig status update cron wrapper
│   │
│   ├── templates/             # Django templates (base, dashboard, accounts)
│   └── manage.py
│
├── scripts/                   # Developer / testing helper scripts
│   │                         # These are NOT production artifacts.
│   │                         # Run locally, never deployed to production.
│   ├── sync_to_opt.sh         # Full deploy: workspace → /opt (rsync + migrate)
│   ├── sync_agent.sh          # Agent files only → /opt/monitoring-agent
│   └── sync_and_migrate.sh    # Granular file sync of specific changed files
│
├── docs/                      # Documentation
│   ├── GPU_Rig_Monitoring_Architecture.md  # Full architecture reference
│   ├── DEPLOYMENT_GUIDE.md                 # Production deployment (VPS + TLS)
│   ├── LOCAL_DEPLOYMENT_GUIDE.md           # Local testing (no domain needed)
│   └── ...                                 # Additional analysis/spec docs
│
└── README.md                  # This file
```

## Directory Conventions

### `gpu_monitor/deploy/` — Production Artifacts
Files in this directory are **production deployment files**. They are:
- Copied to `/opt/gpu_monitor/deploy/` during deployment
- Referenced by cron jobs, systemd units, and nginx configs in production
- Required for the running system

Examples: `nginx.conf`, `gunicorn.service`, `server_install.sh`, `data_retention.sh`, `update_rig_status.sh`

### `scripts/` — Dev/Testing Helpers
Files in this directory are **developer convenience scripts** for the local workflow only. They:
- Are run **locally by the developer** (not deployed to production)
- Are never referenced by cron, systemd, or any production config
- Should NOT contain production cron wrappers or deploy artifacts

Examples: `sync_to_opt.sh` (workspace → /opt), `sync_agent.sh` (agent only), `sync_and_migrate.sh` (granular sync)

> **Rule of thumb:** If a file is referenced by a cron job or systemd unit in production, it belongs in `gpu_monitor/deploy/`, **not** in `scripts/`.

### `agent/` and `agent_windows/` — Monitoring Agents
Deployed to each monitored rig. Collect hardware metrics (CPU, GPU, memory, storage, network, Docker) and POST them to the server every 60 seconds. See each directory's `README.md` for detailed setup instructions.

## What Gets Collected

| Category | Metrics | Agent |
|----------|---------|-------|
| **CPU** | Model, cores, load avg, temperature, utilization % | Linux, Windows |
| **Memory** | Total, used, free, cached, swap | Linux, Windows |
| **GPU** | Model, memory (used/free/total), utilization, temp, power, fan, PCIe link | Linux, Windows |
| **GPU Processes** | Per-process name, type (C/G/C+G), memory usage | Linux, Windows |
| **Storage** | Per-device capacity, usage %, SMART health, NVMe logs, temperature | Linux, Windows |
| **Network** | Per-interface IPv4, speed, RX/TX bytes, error counts | Linux, Windows |
| **Docker** | Container count, names, images, status, restart count | Linux, Windows |
| **Motherboard** | Manufacturer, model, BIOS version | Linux, Windows |
| **Software** | Hostname, OS distro, kernel, uptime, NVIDIA driver, Docker version | Linux, Windows |
| **Errors** | System errors from last 5 minutes (journalctl / Windows Event Log) | Linux, Windows |

## Dashboard Features

| Tab | Description |
|-----|-------------|
| **Live Metrics** | Auto-refreshing cards (CPU, Memory, GPU, Storage, Network, Docker, Errors) via 30s HTMX polling |
| **Historical Charts** | Multi-series time-range charts (GPU temp/util/memory/power, CPU, Storage, Network, Error frequency) |
| **Errors** | Recent system errors with source, timestamp, and count |

**Fleet overview:** All rigs with per-GPU summaries, tag filtering, status badges (🟢 Online / 🟡 Stale / 🔴 Offline).

**Rig status state machine:**
- 🟢 **Online** — last seen ≤ 2 minutes ago
- 🟡 **Stale** — last seen 2–10 minutes ago
- 🔴 **Offline** — last seen > 10 minutes ago

## Development Workflow

1. Fix/test in workspace (`/home/qrv/workspace/GPU-Rig-Monitoring-Platform/`)
2. Sync to `/opt` for testing:
   ```bash
   # Full sync (code + migrations + restart):
   bash scripts/sync_to_opt.sh
   # Fast sync (skip migration check):
   bash scripts/sync_to_opt.sh --no-migrate
   # Agent only:
   sudo bash scripts/sync_agent.sh
   ```
3. Verify, then commit to a feature branch
4. Push and create a pull request for review

## Security

- **Per-rig rate limiting:** 5 req/min per `rig_uuid` (no IP-based blocking)
- **General rate limiting:** 30 req/s per IP (burst protection)
- **TLS 1.3** via Let's Encrypt (auto-renewed)
- **Timestamp validation:** payloads with timestamps >5 min future or >1 hour past are rejected (400)
- **Dual authentication:** `X-API-Key` (user) + `X-Rig-UUID` (rig identification)
- **Agent isolation:** agents authenticate via API key only — no access to other users' rigs

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Server framework | Django 6.x + Django REST Framework |
| Database | PostgreSQL 16 + TimescaleDB (hypertables) |
| Task runner | Gunicorn (WSGI) |
| Web server | Nginx (reverse proxy, TLS termination) |
| Frontend | Django Templates + HTMX (server-rendered, no SPA) |
| Auth | Email/password + API keys + session cookies |
| Agents | Python 3.10+, psutil, pynvml, WMI (Windows) |
| Scheduler | Linux: cron · Windows: Task Scheduler |
