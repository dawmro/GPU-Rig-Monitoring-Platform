# GPU Rig Monitoring Platform — Architecture Document

**Version:** 1.0
**Status:** Implemented — Living Architecture Reference
**Last Updated:** 2026-06-02

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [Client Agent Specification](#3-client-agent-specification)
4. [Server Architecture](#4-server-architecture)
5. [Dashboard Specification](#5-dashboard-specification)
6. [Data Model Reference](#6-data-model-reference)
7. [Security Trust Boundaries](#7-security-trust-boundaries)
8. [Operational Runbook](#8-operational-runbook)
9. [Design Decisions & Rationale](#9-design-decisions--rationale)

---

## 1. Executive Summary

The GPU Rig Monitoring Platform is a single-server telemetry dashboard for GPU rigs running AI/LLM workloads. It uses Django + HTMX for server-rendered HTML with live polling, PostgreSQL for data storage, and a lightweight Python agent for metric collection.

**Topology:** One Ubuntu VPS hosts everything (Django, Nginx, PostgreSQL, Gunicorn). Remote rigs run the agent via cron and POST telemetry to the server over HTTP.

**Scale target:** ~1,000 rigs reporting at 1-minute intervals.

### 1.1 Non-Goals (v1)

| Excluded | Rationale |
|----------|-----------|
| Full log aggregation (ELK, Loki) | Only latest deduplicated errors with timestamps |
| Active alerting (PagerDuty, Slack) | Visual dashboard indicators only |
| Distributed deployment / K8s | Single VPS intentionally |
| Public API / webhooks | API is internal-only, scoped to agents |
| Remote command execution | Agent is strictly read-only |

### 1.2 Success Metrics

| Metric | Target |
|--------|--------|
| Payload acceptance rate | > 99.9% |
| Agent overhead (CPU) | < 2% |
| Payload size | < 2 KB compressed |
| Time to first payload | < 2 min from agent install |

---

## 2. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    REMOTE RIGS (Untrusted)                   │
│                                                              │
│  ┌──────────────┐  HTTP POST   ┌────────────┐              │
│  │ Agent (cron) │ ──────────→  │   Nginx :80│  (or :443)   │
│  └──────────────┘              └─────┬──────┘              │
└──────────────────────────────────────┼───────────────────────┘
                                       │ proxy to 8000
┌──────────────────────────────────────┼───────────────────────┐
│                 UBUNTU VPS           │                      │
│                              ┌───────▼──────┐               │
│  Browser ←──HTMX polling──→  │ Gunicorn :8000│              │
│  Django ←──render──────────→  │   (4 workers) │              │
│                              └───────┬──────┘               │
│                                      │                       │
│                              ┌───────▼──────┐               │
│                              │ PostgreSQL   │               │
│                              │   :5432      │               │
│                              └──────────────┘               │
└──────────────────────────────────────────────────────────────┘
```

### 2.1 Service Matrix

| Service | Port | Protocol | Access |
|---------|------|----------|--------|
| Nginx | 80/443 | HTTP/HTTPS | Public |
| Gunicorn | 8000 | HTTP | localhost only |
| PostgreSQL | 5432 | TCP | localhost only |

### 2.2 Data Flow: Agent Ingestion

```
Cron → Agent collects metrics → JSON payload → POST /api/v1/ingest/
  → Nginx (rate limit, size check)
  → DRF APIKeyAuthentication (X-API-Key header → Argon2id hash comparison)
  → DRF throttle (per-key rate limit)
  → IngestSerializer validation (schema version 1.0)
  → process_ingest() → DB upsert (MetricSnapshot, GPUMetric, StorageMetric, etc.)
  → Rig.last_seen and Rig.status updated to ONLINE
  → Response: 200 (new) or 202 (duplicate/idempotent)
```

### 2.3 Key Files

| File | Purpose |
|------|---------|
| `agent/run.py` | Linux agent (~480 lines) |
| `agent_windows/run.py` | Windows agent (~890 lines) |
| `metrics_app/views.py` | IngestView, HealthView, ChartDataView, RigMetricsView |
| `metrics_app/serializers.py` | IngestSerializer, process_ingest() |
| `metrics_app/models.py` | MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, DockerContainerMetric, LatestSnapshot, ErrorEvent |
| `dashboard/views.py` | rig_list, rig_detail, htmx_metrics, htmx_rig_status, rig_rename |
| `rigs/models.py` | Rig, RigTag |
| `accounts/authentication.py` | APIKeyAuthentication |
| `rigs/management/commands/update_rig_status.py` | Rig status state machine |

---

## 3. Client Agent Specification

### 3.1 Runtime

| Property | Value |
|----------|-------|
| Python | 3.10+ |
| Path | `/opt/monitoring-agent/` (Linux) or next to `run.py` (Windows) |
| Deps | psutil, pynvml, py-cpuinfo, requests, pyyaml |
| User | `monitoring-agent` system user (Linux) |
| Schedule | Every 60 seconds via cron |

### 3.2 Configuration (`/etc/monitoring-agent/config.yaml`)

```yaml
rig_uuid: "auto"          # Auto-generated on first run, persisted to file
rig_name: ""              # Suggested initial name. Used ONLY once during rig creation.
                          # Leave empty to use hostname. Ignored on subsequent updates.
api_key: "..."            # Server-side API key (shown once at creation)
server_endpoint: "http://..."  # Must include http:// or https://
expected_gpu_count: 0     # 0 = auto-detect
collection_timeout_s: 45  # Hard timeout via signal.alarm()
retry_attempts: 3         # Exponential backoff: 1s → 2s → 4s
debug_mode: false         # Verbose logging
```

### 3.3 Payload Schema (v1.0)

```json
{
  "rig_uuid": "UUIDv4",
  "rig_name": "my-server",
  "schema_version": "1.0",
  "agent_version": "1.0.0",
  "timestamp": "2026-06-02T19:54:06Z",
  "inventory": { "cpu": {}, "memory": {}, "motherboard": {}, "storage": [], "network": [], "gpus": [] },
  "metrics": { "cpu": {}, "memory": {}, "storage": [], "network": [], "gpus": [], "ai_processes": [], "docker_containers": [] },
  "software": { "hostname": "...", "os_distro": "...", "kernel": "..." },
  "errors": [{ "source": "kernel", "message": "...", "timestamp": "..." }]
}
```

### 3.4 Transport

- **Compression:** None (Django DRF does not auto-decompress gzip request bodies)
- **Idempotency:** Same `rig_uuid + schema_version + timestamp` → 202 Accepted (not 200)
- **Retry:** Exponential backoff with jitter, max 3 attempts, 45s hard timeout

### 3.5 Two Agents

| Agent | File | Platform | Scheduling |
|-------|------|----------|------------|
| Linux | `agent/run.py` | Any Linux, VMware NAT | `cron` every 60s with `flock` |
| Windows | `agent_windows/run.py` | Windows 10/11 | Task Scheduler with `pythonw.exe` (hidden window) |

---

## 4. Server Architecture

### 4.1 Django Apps

| App | Models | Key Views |
|-----|--------|-----------|
| `gpu_monitor` | — | Settings, URL routing, WSGI |
| `accounts` | User, ApiKey | Login, logout, API key management |
| `rigs` | Rig, RigTag | `update_rig_status` management command |
| `metrics_app` | MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, DockerContainerMetric, LatestSnapshot, ErrorEvent | IngestView, HealthView, ChartDataView, RigMetricsView |
| `dashboard` | — | rig_list, rig_detail, htmx_metrics, htmx_rig_status, rig_rename |
| `audit` | AuditLog | Middleware-based request logging |
| `dashboard/templatetags` | — | gpu_model_name, gpu_model_short filters |

### 4.2 Authentication

| Context | Mechanism |
|---------|-----------|
| Agent ingestion | `X-API-Key` header → `APIKeyAuthentication` (Argon2id hash comparison) |
| Dashboard | Django session cookie (Secure, HttpOnly, SameSite=Lax) |
| API key creation | Session auth + `login_required` |

### 4.3 Ingestion Pipeline

```
POST /api/v1/ingest/
  → CsrfViewMiddleware (skipped via @csrf_exempt on IngestView)
  → APIKeyAuthentication validates X-API-Key
  → DRF throttle (AnonRateThrottle)
  → IngestSerializer validates schema
  → process_ingest() in transaction.atomic():
      - Upsert MetricSnapshot (cpu, memory fields)
      - Upsert GPUMetric per GPU (gpu_index = 0, 1, ...)
      - Upsert StorageMetric per disk
      - Upsert NetworkMetric per interface
      - Upsert DockerContainerMetric per container
      - Upsert ErrorEvent (deduplicated by hash)
      - Update Rig.last_seen = now(), Rig.status = ONLINE
      - On Rig.DoesNotExist: auto-create with agent-suggested name
        (rig_name is used only at creation, never overwritten)
  → Response: {"status": "new"} or {"status": "duplicate"}
```

### 4.4 Rig Name Management (Design Decision: Option 5)

**Problem:** Two sources of truth — agent's `config.yaml` rig_name and dashboard database field.

**Solution:** Agent sends `rig_name` in every payload, but the server uses it **only** when creating a new rig (`Rig.DoesNotExist`). All subsequent renames are dashboard-only via the `rig_rename` POST endpoint. This prevents config.yaml from overwriting user-changed names on every heartbeat.

### 4.5 Rig Status State Machine

Managed by `update_rig_status` management command, run every 2 minutes via cron:

```
ONLINE  → STALE   (last_seen > 2 minutes ago)
STALE   → OFFLINE (last_seen > 10 minutes ago)
ONLINE  → OFFLINE (last_seen > 10 minutes ago, direct transition)
```

On every agent heartbeat, `IngestView` sets `Rig.status = ONLINE` and `Rig.last_seen = now()`.

### 4.6 API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/ingest/` | API Key | Telemetry submission |
| GET | `/api/v1/health/` | None | Health check (DB + active rigs count) |
| GET | `/api/v1/rigs/<uuid>/metrics/` | Session | Latest metrics (used by Chart.js direct fetch) |
| GET | `/api/v1/rigs/<uuid>/chart-data/?metric=X&range=N` | Session | Historical chart data |
| GET | `/dashboard/rigs/` | Session | Fleet Overview (HTMX) |
| GET | `/dashboard/rigs/<uuid>/htmx-metrics/` | Session | Live metrics partial (30s poll) |
| GET | `/dashboard/rigs/<uuid>/htmx-status/` | Session | Status badge partial (15s poll) |
| POST | `/dashboard/rigs/<uuid>/rename/` | Session | Rename rig |

### 4.7 Cron Jobs

| Job | Frequency | Wrapper |
|-----|-----------|---------|
| Linux agent | 60s | `flock` + cron |
| Rig status update | 2 min | `scripts/update_rig_status.sh` |
| Frontend (Windows) agent | 60s | Task Scheduler + `pythonw.exe` |

---

## 5. Dashboard Specification

### 5.1 HTMX Polling Architecture

HTMX polls use `hx-swap="innerHTML"` (not `outerHTML`). This is critical: `innerHTML` replaces only the **contents** of the target element, preserving the wrapper `<div>` that carries the `hx-*` attributes. Using `outerHTML` would destroy the wrapper on the first swap, breaking all subsequent polls.

**Global refresh clock:** A single `htmx:afterSwap` event listener in `base.html` updates all clock spans (`<target>-clock`) after every swap, giving the operator visibility into when each section was last refreshed.

### 5.2 Fleet Overview (`/dashboard/rigs/`)

**Polling:** `#rig-table-container` polls every 30s via `innerHTML` swap.

**Data source:** `dashboard/views.py rig_list()` queries:
- `Rig` (all fields including `status`, `last_seen`, `name`)
- `LatestSnapshot` (cpu_utilization_pct, mem_used_bytes, cpu_temp_c, etc.)
- `GPUMetric` (gpu_index=0: gpu_temp_c, gpu_util_pct, model)

**Sorting:** Alphabetically by `Rig.name` (stable ordering, rigs don't jump around).

**Columns and data sources:**

| Column | Source | Model Field |
|--------|--------|-------------|
| Rig Name | Rig.name | Clickable link to detail |
| Status | Rig.status | Online/Stale/Offline |
| Last Seen | Rig.last_seen | Relative time |
| Tags | RigTag M2M | Colored pills |
| GPU | GPUMetric.model | Cleaned by gpu_model_name filter |
| GPU Temp | GPUMetric.gpu_temp_c | Color-coded thresholds |
| GPU Util | GPUMetric.gpu_util_pct | Percentage |
| CPU | LatestSnapshot.cpu_utilization_pct | Percentage |

### 5.3 Rig Detail Page (`/dashboard/rigs/<uuid>/`)

Three HTMX polling regions:

| Region | Target ID | Interval | Mode | Data |
|--------|-----------|----------|------|------|
| Status badge | `#rig-status-container` | 15s | `innerHTML` | Rig.status, Rig.last_seen |
| Live metrics | `#metrics-container` | 30s | `innerHTML` | CPU, memory, GPU, Docker, storage, errors, error events |
| Header status | — | 15s | HTMX badge + clock | Status + last_seen |

Plus one manual-refresh region:

| Region | Trigger | Data |
|--------|---------|------|
| Historical charts | User clicks ↻ button | 7× ChartDataView queries (GPU temp, GPU util, GPU mem, GPU power, CPU util, CPU temp, memory) |

**Historical charts are NOT polled automatically** — they load once when the tab is first opened and refresh only when the user clicks the ↻ button. This avoids 7× expensive time-series queries (2000 rows each) every 30 seconds.

### 5.4 Tab Layout

The rig detail page has three tabs:

1. **Live Metrics** — cards with CPU%, memory bar, GPU model/temp/util/power/vRAM, Docker container count, storage disks, recent errors
2. **Historical Charts** — 7 individual Chart.js line charts (GPU temp, GPU util, GPU VRAM, GPU power, CPU util, CPU temp, memory usage) with a ↻ Refresh button in the tab header
3. **Errors** — recent system errors from journalctl/Windows Event Log

### 5.5 Data Deduplication

Storage metrics are deduplicated by device: the view queries the latest `StorageMetric` per unique `device` path, preventing duplicate entries when the agent reports the same disk multiple times within the window.

Time window for HTMX metrics: 1 hour (not 5 minutes) to handle gaps when the agent misses a heartbeat.

---

## 6. Data Model Reference

### 6.1 Table Summary

| Table | App | Purpose |
|-------|-----|---------|
| `accounts_user` | accounts | Custom user model (email-based) |
| `accounts_apikey` | accounts | API keys for agent ingestion (Argon2id hashed) |
| `rigs_rig` | rigs | Rig inventory (uuid PK, owner FK, status, last_seen, name) |
| `rigs_rigtag` | rigs | Tags (name, color) |
| `rigs_rig_tags` | rigs | M2M through table |
| `metrics_metricsnapshot` | metrics_app | Per-heartbeat metrics (cpu, memory fields inline) |
| `metrics_gpumetric` | metrics_app | Per-GPU metrics (temp, util, mem, power; FK to snapshot) |
| `metrics_storagemetric` | metrics_app | Per-disk metrics (usage, smart temp) |
| `metrics_networkmetric` | metrics_app | Per-interface metrics (rx/tx bytes, speed) |
| `metrics_dockercontainermetric` | metrics_app | Per-container metrics (name, status, restarts) |
| `metrics_latestsnapshot` | metrics_app | Current state per rig (upserted on every heartbeat) |
| `metrics_errorevent` | metrics_app | Deduplicated errors (hash-based dedup, count, last_seen) |
| `audit_auditlog` | audit | Immutable audit trail |

### 6.2 Key Constraints

| Table | Constraint |
|-------|------------|
| `metrics_gpumetric` | `UNIQUE(rig_uuid, timestamp, gpu_index)` |
| `metrics_storagemetric` | `UNIQUE(rig_uuid, timestamp, device)` |
| `metrics_networkmetric` | `UNIQUE(rig_uuid, timestamp, interface)` |
| `metrics_dockercontainermetric` | `UNIQUE(rig_uuid, timestamp, name)` |
| `metrics_metricsnapshot` | `UNIQUE(rig_uuid, schema_version, timestamp)` |

### 6.3 Metric Field Name Mapping

The `ChartDataView` uses a name-mapping dict because chart-facing metric names differ from model field names:

| Chart Metric (URL param) | GPUMetric Model Field |
|--------------------------|----------------------|
| `gpu_temp_c` | `gpu_temp_c` |
| `gpu_util_pct` | `gpu_util_pct` |
| `gpu_mem_used_mb` | `mem_used_mb` |
| `gpu_mem_total_mb` | `mem_total_mb` |
| `gpu_power_w` | `power_draw_w` |
| `gpu_power_limit_w` | `power_limit_w` |
| `gpu_fan_pct` | `fan_speed_pct` |

---

## 7. Security Trust Boundaries

| Zone | Components | Enforcement |
|------|-----------|-------------|
| Z1: Fleet (untrusted) | Agents, internet | HTTP (local) or HTTPS, API key auth |
| Z2: Edge | Nginx | Rate limiting, payload size caps |
| Z3: Application | Django, Gunicorn | Session auth, CSRF, ownership checks |
| Z4: Data | PostgreSQL | localhost-only, least-privilege DB user |

**CSRF exemption:** `IngestView` uses `@csrf_exempt` because it authenticates via API key (not session cookie). The agent has no CSRF token.

**Cookie settings for local testing:**
```
SESSION_COOKIE_SECURE = False    # HTTP without TLS
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
ALLOWED_HOSTS = '*'              # Accept any host (local testing only)
CSRF_TRUSTED_ORIGINS = ['http://*', 'https://*']
```

---

## 8. Operational Runbook

### 8.1 Deployment Procedure

```bash
# Sync code from workspace to /opt
cd /home/qrv/workspace/GPU-Rig-Monitoring-Platform
bash scripts/sync_to_opt.sh
# This: copies files, runs migrations, restarts Gunicorn
```

### 8.2 Log Locations

| Log | Path | Format |
|-----|------|--------|
| Gunicorn errors | `/opt/gpu_monitor/logs/gunicorn-error.log` | stdout |
| Gunicorn access | `/opt/gpu_monitor/logs/gunicorn-access.log` | HTTP access |
| Django app | `/opt/gpu_monitor/logs/app.log` | Structured JSON |
| Agent (Linux) | `/var/log/monitoring-agent/agent.log` | Structured JSON |
| Agent cron | `/var/log/monitoring-agent/cron.log` | stdout |

### 8.3 Manual Operations

```bash
# Restart Gunicorn
sudo systemctl restart gunicorn

# Restart cron (needed after creating new cron jobs)
sudo systemctl restart cron

# Test agent
sudo -u monitoring-agent /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/run.py

# Update rig status manually
cd /opt/gpu_monitor && source venv/bin/activate && set -a && source .env && set +a && python manage.py update_rig_status

# View HTMX polling
curl -s http://localhost/api/v1/health | python3 -m json.tool

# Database console
sudo -u postgres psql gpu_monitor
```

### 8.4 Sync Script (`scripts/sync_to_opt.sh`)

The sync script handles the full deploy cycle:
1. Copies all Django source code, templates, migrations, agents, scripts from workspace to `/opt`
2. Runs `python manage.py makemigrations --check` in `/opt` to detect model changes
3. Runs `python manage.py migrate` to apply any new migrations
4. Runs `python manage.py collectstatic`
5. Restarts Gunicorn

Files **never** overwritten: `.env`, `venv/`, `logs/`, `staticfiles/`, `config.yaml`, cron jobs, PostgreSQL database.

---

## 9. Design Decisions & Rationale

### 9.1 Why HTMX `innerHTML` instead of `outerHTML`?

**Problem discovered:** Using `outerHTML` swap on a div with `hx-*` attributes destroys the wrapper on the first swap. The server returns only the inner content (e.g., `_rig_table.html` starts with `<div class="bg-gray-800...">`, not `<div id="rig-table-container" hx-get="...">`). After swap, the element no longer has `hx-*` attributes and polling stops permanently.

**Solution:** Use `innerHTML` which replaces only the contents, preserving the wrapper div.

### 9.2 Why Historical Charts Don't Poll Automatically?

Each chart fetches up to 2000 rows from the database. With 7 charts × N tabs open, polling every 30 seconds would add significant load for minimal value (historical data only changes at the latest data point). The ↻ button gives the user control.

### 9.3 Why Rig Name Is Set Only Once?

Two sources of truth conflict: agent's `config.yaml` vs dashboard database.Setting the name on every heartbeat would overwrite dashboard renames. Solution: agent's `rig_name` is used only during initial rig creation. Subsequent renames use the dashboard-only `rig_rename` POST endpoint.

### 9.4 Why Deduplicate Storage Metrics?

Without deduplication, a single heartbeat creates N records per disk (one per poll window), causing N duplicate rows to appear in the dashboard "Storage" card. The view deduplicates by taking only the latest record per unique `device` path.

### 9.5 Why 1-Hour Window for HTMX Metrics?

A 5-minute window was too narrow — if the agent misses one heartbeat (common in VMs under load), the metrics show "No data". A 1-hour window provides tolerance while still returning recent data.

### 9.6 Why `flex flex-col items-end` for Status Clock?

The rig detail header uses `flex justify-between` with two children (rig name left, status right). Adding a third child (clock) would center it between them. Wrapping the clock above the status badge in a single `flex flex-col items-end` container makes the header have only two flex children again, with the clock positioned above the badge, right-aligned.

### 9.7 Why `pythonw.exe` for Windows Agent?

`pythonw.exe` runs Python without a visible terminal window, preventing a console window from appearing every 60 seconds. The `create_windows_task()` function tries `pythonw.exe` first and falls back to `python.exe` if not found.
