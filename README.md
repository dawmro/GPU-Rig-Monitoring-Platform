# GPU Rig Monitoring Platform

A single-server telemetry dashboard for GPU rigs running AI/LLM workloads.

## Quick Start

See [`docs/LOCAL_DEPLOYMENT_GUIDE.md`](docs/LOCAL_DEPLOYMENT_GUIDE.md) for a step-by-step local setup, or [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) for production deployment.

## Repository Structure

```
GPU-Rig-Monitoring-Platform/
├── agent/                     # Linux monitoring agent (deployed to rigs)
│   ├── run.py                 # Agent script (psutil + pynvml metric collection)
│   ├── config.yaml.example    # Config template
│   └── install.sh             # Installer (creates user, venv, cron)
│
├── agent_windows/             # Windows monitoring agent (deployed to rigs)
│   ├── run.py                 # Agent script (WMI instead of sysfs)
│   ├── config.yaml.example
│   └── README.md
│
├── gpu_monitor/               # Django project (deployed to /opt/gpu_monitor/)
│   ├── gpu_monitor/           # Django settings package (settings, urls, wsgi)
│   ├── accounts/              # Auth app (email login, API key management)
│   ├── rigs/                  # Rig inventory (Rig model, status state machine)
│   ├── metrics_app/           # GPU metrics data layer (ingestion API, models)
│   ├── dashboard/             # HTMX dashboard (views, templates, tab tags)
│   ├── audit/                 # Audit logging middleware
│   ├── templates/             # Django templates (base, dashboard, accounts)
│   ├── deploy/                # PRODUCTION deployment artifacts
│   │   ├── nginx.conf         # Nginx site config
│   │   ├── gunicorn.service   # Gunicorn systemd unit
│   │   ├── server_install.sh  # Full production server installer
│   │   └── update_rig_status.sh  # Cron wrapper for rig status updates
│   └── manage.py
│
├── scripts/                   # Developer / testing helper scripts
│   │                         # These are NOT production artifacts.
│   │                         # They are convenience scripts for local dev
│   │                         # workflow only (run locally, not deployed).
│   ├── sync_to_opt.sh         # Full deploy: workspace → /opt (rsync + migrate)
│   └── sync_and_migrate.sh    # Granular sync of specific changed files
│
├── docs/                      # Documentation
│   ├── GPU_Rig_Monitoring_Architecture.md  # Architecture reference
│   ├── DEPLOYMENT_GUIDE.md                 # Production deployment
│   └── LOCAL_DEPLOYMENT_GUIDE.md           # Local testing deployment
│
└── README.md                  # This file
```

## Directory Conventions

### `gpu_monitor/deploy/` — Production Artifacts
Files in this directory are **production deployment files**. They are:
- Copied to `/opt/gpu_monitor/deploy/` during deployment
- Referenced by cron jobs, systemd units, and nginx configs in production
- Required for the running system

Examples: `nginx.conf`, `gunicorn.service`, `server_install.sh`, `update_rig_status.sh`

### `scripts/` — Dev/Testing Helpers
Files in this directory are **developer convenience scripts** for the local workflow only. They:
- Are run **locally by the developer** (not deployed to production)
- Are never referenced by cron, systemd, or any production config
- Should NOT contain production cron wrappers or deploy artifacts

Examples: `sync_to_opt.sh` (full workspace → /opt deploy), `sync_and_migrate.sh` (granular file sync), `sync_agents.sh` (agent files only)

> **Rule of thumb:** If a script is referenced by a cron job or systemd unit in production, it belongs in `gpu_monitor/deploy/`, **not** in `scripts/`.

### `agent/` and `agent_windows/` — Monitoring Agents
These are deployed to each monitored rig. They collect hardware metrics (CPU, GPU, memory, storage, network, Docker) and POST them to the server every 60 seconds.

### Architecture

See [`docs/GPU_Rig_Monitoring_Architecture.md`](docs/GPU_Rig_Monitoring_Architecture.md) for the full architecture document including data flow, security boundaries, API reference, and troubleshooting.
