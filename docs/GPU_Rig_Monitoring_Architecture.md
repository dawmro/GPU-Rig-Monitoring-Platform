# GPU Rig Monitoring Platform — Architecture Document

**Version:** 1.3
**Status:** Implemented — Living Architecture Reference
**Last Updated:** 2026-06-10

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
10. [Performance & Scaling](#10-performance--scaling)
11. [Testing Strategy](#11-testing-strategy)
12. [Troubleshooting](#12-troubleshooting)
13. [File Locations Reference](#13-file-locations-reference)
14. [Appendices](#14-appendices)

---

## 1. Executive Summary

The GPU Rig Monitoring Platform is a single-server telemetry dashboard for GPU rigs running AI/LLM workloads. It uses Django + HTMX for server-rendered HTML with live polling, PostgreSQL for data storage, and a lightweight Python agent for metric collection.

**Topology:** One Ubuntu VPS hosts everything (Django, Nginx, PostgreSQL, Gunicorn). Remote rigs run the agent via cron and POST telemetry to the server over HTTP.

**Scale target:** ~1,000 rigs reporting at 1-minute intervals.

**Measured storage per rig (100% uptime):** ~4.7 MB/day
- At 50% uptime: ~2.35 MB/day
- 31-day retention with tiered compaction: ~7 GB total for 1,000 rigs
- Without compaction: ~146 GB for 1,000 rigs

**Data retention:** 31 days (matches 30-day max chart range + 1 day safety margin)
- 0-1 day: raw per-minute data
- 1-31 days: compacted to 1-hour buckets
- 31+ days: deleted

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
  → Nginx (rate limit: per-rig 5/min + per-IP 30/s, payload size check)
  → DRF APIKeyAuthentication (X-API-Key header → Argon2id hash comparison)
  → DRF throttle (per-rig rate limit, scoped by rig_uuid)
  → Timestamp sanity check (reject if >5 min future or >1 hour past)
  → IngestSerializer validation (schema version 1.0, 1.1, or 1.2)
  → process_ingest() → DB upsert (MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, DockerContainerMetric, AIProcessMetric, RigStatusEvent, LatestSnapshot)
  → Rig.latest_errors_json updated with latest error text
  → Rig.last_seen and Rig.status updated to ONLINE
  → Response: 200 (new) or 202 (duplicate/idempotent)
```

### 2.3 Key Files

| File | Purpose |
|------|---------|
| `agent/run.py` | Linux agent (~517 lines) |
| `agent_windows/run.py` | Windows agent (~916 lines) |
| `metrics_app/views.py` | IngestView, HealthView, ChartDataView, RigMetricsView |
| `metrics_app/serializers.py` | IngestSerializer, process_ingest() |
| `metrics_app/models.py` | MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, DockerContainerMetric, LatestSnapshot, RigStatusEvent, AIProcessMetric |
| `dashboard/views.py` | rig_list, rig_detail, htmx_metrics, htmx_rig_status, rig_rename |
| `dashboard/templatetags/gpu_filters.py` | gpu_model_name, gpu_model_short, gpu_compact_summary, gpu_temp_cell, gpu_util_cell, gpu_fan_cell, time_since filters |
| `rigs/models.py` | Rig, RigTag |
| `accounts/authentication.py` | APIKeyAuthentication |
| `rigs/management/commands/update_rig_status.py` | Rig status state machine (creates RigStatusEvent on transitions) |

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

### 3.1b Sudoers Configuration

The agent needs passwordless sudo for read-only hardware queries. Required in `/etc/sudoers.d/monitoring-agent`:

```
Defaults:monitoring-agent !authenticate
monitoring-agent ALL=(root) NOPASSWD: /usr/sbin/smartctl, /usr/bin/smartctl, /bin/journalctl, /usr/bin/journalctl, /usr/sbin/nvme, /usr/bin/nvme
```

**Critical:** `Defaults:monitoring-agent !authenticate` is required. Without it, PAM fails for system users with `nologin` shell:
```
pam_unix(sudo:auth): conversation failed
pam_unix(sudo:auth) auth could not identify password for [monitoring-agent]
```
`NOPASSWD` alone is insufficient — `!authenticate` tells sudo to skip PAM entirely.

**Commands:** `smartctl` (disk SMART), `nvme` (NVMe health), `journalctl` (system errors). All read-only.

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

### 3.3 Payload Schema (v1.1)

```json
{
  "rig_uuid": "UUIDv4",
  "rig_name": "my-server",
  "schema_version": "1.2",
  "agent_version": "1.2.0",
  "timestamp": "2026-06-07T19:54:06Z",
  "metrics": {
    "cpu": {
      "model": "AMD Ryzen 7 5700X3D 8-Core Processor",
      "physical_cores": 8,
      "logical_cores": 16,
      "load_avg": [0.26, 0.23, 0.36],
      "utilization_pct": 5.2,
      "temp_c": null
    },
    "memory": {
      "total_bytes": 68637540352,
      "used_bytes": 27017158656,
      "free_bytes": 41620381696,
      "cached_bytes": null,
      "swap_used_bytes": 368766976,
      "swap_total_bytes": 8589934592
    },
    "storage": [
      {
        "device": "C:\\",
        "mountpoint": "C:\\",
        "fstype": "NTFS",
        "capacity_bytes": 1000200990720,
        "usage_pct": 51.7,
        "temp_c": null,
        "smart_health": ""
      }
    ],
    "network": [
      {
        "interface": "Ethernet",
        "rx_bytes": 143633646149,
        "tx_bytes": 8690378455,
        "rx_errors": 0,
        "tx_errors": 0,
        "ipv4": "192.168.8.158",
        "link_speed_mbps": 100
      }
    ],
    "gpus": [
      {
        "uuid": "GPU-a322cff7-19cf-f056-4a38-b676c04a38aa",
        "model": "NVIDIA GeForce RTX 3060",
        "mem_total_mb": 12288,
        "mem_used_mb": 1235,
        "mem_free_mb": 11052,
        "mem_util_pct": 10.1,
        "gpu_util_pct": 4,
        "temp_c": 46,
        "fan_speed_pct": 0,
        "power_draw_w": 8.843,
        "power_limit_w": 170.0
      }
    ],
    "gpu_processes": [
      {
        "gpu_index": 0,
        "pid": 2247,
        "type": "G",
        "name": "/usr/lib/xorg/Xorg",
        "gpu_mem_mb": 6
      },
      {
        "gpu_index": 0,
        "pid": 3199,
        "type": "C",
        "name": "./srbminer_custom_bin",
        "gpu_mem_mb": 2936
      }
    ],
    "ai_processes": [
      {
        "process_name": "ollama",
        "pid": 1234,
        "gpu_uuid": "GPU-a322cff7-19cf-f056-4a38-b676c04a38aa",
        "gpu_mem_used_mb": 8000,
        "cpu_pct": 15.5
      }
    ],
    "docker_containers": [
      {
        "name": "ollama",
        "image": "ollama/ollama:latest",
        "status": "running",
        "restart_count": 0,
        "cpu_pct": 15.5,
        "mem_usage_bytes": 4000000000,
        "mem_limit_bytes": 8000000000
      }
    ]
  },
  "motherboard": {
    "manufacturer": "Gigabyte Technology Co., Ltd.",
    "model": "B450M DS3H-CF",
    "bios_version": "F67d"
  },
  "software": {
    "hostname": "DESKTOP-REE04FV",
    "os_distro": "Windows-10-10.0.19045-SP0",
    "kernel": "10",
    "uptime_s": 2415271,
    "nvidia_driver": "571.96",
    "docker_version": "24.0.7"
  },
  "errors": [
    {
      "source": "kernel",
      "message": "nvidia-container-cli failed",
      "timestamp": "2026-06-02T19:54:06"
    }
  ]
}
```

**Changelog from schema 1.1 → 1.2:**
- Added `gpu_processes[]` array with per-GPU process data from nvidia-smi
- Each process: `gpu_index`, `pid`, `type` (C/G/C+G), `name`, `gpu_mem_mb`
- Added `GPUProcessMetric` model for server-side storage (latest snapshot only, delete-before-insert pattern)
- Server deduplicates by deleting all old process rows per rig before inserting new ones

### 3.4 Transport

- **Compression:** None (Django DRF does not auto-decompress gzip request bodies)
- **Idempotency:** Same `rig_uuid + schema_version + timestamp` → 202 Accepted (not 200)
- **Retry:** Exponential backoff with jitter, max 3 attempts, 45s hard timeout

### 3.5 Two Agents

| Agent | File | Version | Schema | Platform | Scheduling |
|-------|------|---------|--------|----------|------------|
|| Linux | `agent/run.py` | 1.3.0 | 1.3 | Any Linux, VMware NAT | `cron` every 60s with `flock` |
| Windows | `agent_windows/run.py` | 1.4.0-win | 1.3 | Windows 10/11 | Task Scheduler with `pythonw.exe` (hidden window) |

**Versioning rules:**
- `agent_version` (e.g. `1.1.0`): incremented for agent-side changes (collectors, payload format, bug fixes). Format: `MAJOR.MINOR.PATCH`.
- `schema_version` (e.g. `1.1`): incremented only when the payload structure changes in a way that affects the server's serialization/storage. Format: `MAJOR.MINOR`.
- Schema 1.0 agents remain supported (backward compatible via `validate_schema_version`).
- When schema changes, both `SERIALIZER_MAP` entries are kept (see §11.5).

---

## 4. Server Architecture

### 4.1 Django Apps

| App | Models | Key Views |
|-----|--------|-----------|
| `gpu_monitor` | — | Settings, URL routing, WSGI |
| `accounts` | User, ApiKey | Login, logout, API key management |
| `rigs` | Rig, RigTag | `update_rig_status` management command |
|| `metrics_app` | MetricSnapshot, GPUMetric, GPUProcessMetric, StorageMetric, NetworkMetric, DockerContainerMetric, AIProcessMetric, LatestSnapshot, RigStatusEvent | IngestView, HealthView, ChartDataView, RigMetricsView |
| `dashboard` | — | rig_list, rig_detail, htmx_metrics, htmx_rig_status, rig_rename |
| `audit` | AuditLog | Middleware-based request logging |
| `dashboard/templatetags` | — | gpu_model_name, gpu_model_short, gpu_compact_summary, gpu_temp_cell, gpu_util_cell, gpu_fan_cell, time_since, last_seen_short filters |

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
  → DRF throttle (per-rig rate limit, scoped by rig_uuid)
  → IngestSerializer validation (schema version 1.0, 1.1, 1.2, or 1.3)
  → process_ingest() in transaction.atomic():
      - Upsert MetricSnapshot (cpu, memory, status fields; motherboard/software as JSON; error_count)
      - Upsert GPUMetric per GPU (gpu_index = 0, 1, ...)
      - Delete + recreate GPUProcessMetric per process (latest snapshot only)
      - Upsert StorageMetric per disk (with path-normalized dedup)
      - Upsert NetworkMetric per interface (with rx/tx delta calculation)
      - Upsert DockerContainerMetric per container (with cpu%, memory stats)
      - Upsert AIProcessMetric per AI process (gpu_mem, cpu_pct)
      - Create RigStatusEvent on status transition (e.g. offline→online)
      - Update Rig.latest_errors_json with latest error text from payload
      - Update LatestSnapshot (denormalized cache for fast dashboard loading)
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
| POST | `/api/v1/ingest/` | API Key + X-Rig-UUID header | Telemetry submission (per-rig rate limit, timestamp sanity check) |
| GET | `/api/v1/health/` | None | Health check (DB + active rigs count) |
| GET | `/api/v1/rigs/<uuid>/metrics/` | Session | Latest metrics (used by Chart.js direct fetch) |
| GET | `/api/v1/rigs/<uuid>/chart-data/?metric=X&range=N` | Session | Historical chart data |
| GET | `/dashboard/rigs/` | Session | Fleet Overview (HTMX) |
| GET | `/dashboard/rigs/<uuid>/htmx-metrics/` | Session | Live metrics partial (30s poll) |
| GET | `/dashboard/rigs/<uuid>/htmx-status/` | Session | Status badge partial (15s poll) |
| POST | `/dashboard/rigs/<uuid>/rename/` | Session | Rename rig |

**Agent headers:**
- `X-API-Key`: User's API key (identifies the user/account)
- `X-Rig-UUID`: The rig's UUID (identifies the specific rig, used for per-rig rate limiting)

### 4.7 Cron Jobs

| Job | Frequency | Wrapper |
|-----|-----------|---------|
| Linux agent | 60s | `flock` + cron |
| Rig status update | 2 min | `gpu_monitor/deploy/update_rig_status.sh` |
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
- `GPUMetric` (all GPUs per rig, deduplicated by gpu_uuid)

**Sorting:** Alphabetically by `Rig.name` (stable ordering, rigs don't jump around).

**Columns and data sources:**

| Column | Source | Model Field |
|--------|--------|-------------|
| Rig Name | Rig.name | Clickable link to detail |
| Status | Rig.status | Online/Stale/Offline |
|| Last Seen | Rig.last_seen | Short relative time via `last_seen_short` filter (e.g., '5d, 21h', '45m', 'just now') |
| Tags | RigTag M2M | Colored pills |
| GPU | GPUMetric.model (all GPUs) | Compact summary with count (e.g., "RTX 3060 ×8") |
| GPU Temp [°C] | GPUMetric.gpu_temp_c (all GPUs) | Space-separated color-coded values |
| GPU Util [%] | GPUMetric.gpu_util_pct (all GPUs) | Space-separated color-coded values |
| GPU Fan [%] | GPUMetric.fan_speed_pct (all GPUs) | Space-separated color-coded values |
| CPU [%] | LatestSnapshot.cpu_utilization_pct | Percentage |

### 5.3 Rig Detail Page (`/dashboard/rigs/<uuid>/`)

Three HTMX polling regions:

| Region | Target ID | Interval | Mode | Data |
|--------|-----------|----------|------|------|
| Status badge | `#rig-status-container` | 15s | `innerHTML` | Rig.status, Rig.last_seen |
| Live metrics | `#metrics-container` | 30s | `innerHTML` | CPU, memory, GPU, storage, network, Docker, motherboard, software, errors |
| Header status | — | 15s | HTMX badge + clock | Status + last_seen |

Plus one manual-refresh region:

| Region | Trigger | Data |
|--------|---------|------|
|| Historical charts | User clicks ↻ button | Combined charts: GPU Temperature/Utilization/Memory/Power/Fan Speed (multi-GPU), CPU Utilization/Temperature/Load Average, Memory & Swap (combined, 3 datasets), Disk Usage (multi-disk), Network Traffic (combined RX/TX/Errors, dual Y-axes), Container CPU/Memory (multi-container), AI Process GPU Memory, Uptime, Error Frequency. Timeframe toggle buttons (24h, 7d, 30d) with dynamic label updates. |

**Historical charts are NOT polled automatically** — they load once when the tab is first opened and refresh only when the user clicks the ↻ button. This avoids expensive time-series queries every 30 seconds.

### 5.4 Tab Layout

The rig detail page has three tabs:

1. **Live Metrics** — cards with CPU%, memory bar, GPU model/index/temp/util/power/vRAM, GPU Processes (per-process: name, type badge C/G/C+G, memory), Docker container count, storage disks, recent errors
2. **Historical Charts** — Combined chart suite: GPU (Temperature, Utilization, Memory, Power, Fan Speed — multi-GPU), CPU (Utilization, Temperature, Load Average), Memory & Swap (combined single chart, 3 datasets), Disk Usage (multi-disk), Network Traffic (combined RX/TX/Errors, dual Y-axes), Container CPU/Memory (multi-container), AI Process GPU Memory, Uptime, Error Frequency — all implemented as Chart.js charts with multi-series support. Timeframe toggle buttons (24h, 7d, 30d) in the tab header with a ↻ Refresh button.
3. **Errors** — latest system errors from journalctl/Windows Event Log (stored on Rig model, updated in place)

### 5.5 Data Deduplication

Storage metrics are deduplicated by device: the view queries the latest `StorageMetric` per unique `device` path, preventing duplicate entries when the agent reports the same disk multiple times within the window.

GPU process metrics use a **delete-before-insert** pattern: all existing `GPUProcessMetric` rows for the rig are deleted before inserting the latest snapshot. This ensures only the current process list is stored — no historical process data is needed. The `unique_together` constraint on `(rig_uuid, timestamp, gpu_index, pid)` provides a safety net but is rarely triggered since old rows are deleted first.

Time window for HTMX metrics: 1 hour (not 5 minutes) to handle gaps when the agent misses a heartbeat.

---

## 6. Data Model Reference

### 6.1 Table Summary

| Table | App | Purpose |
|-------|-----|---------|
| `accounts_user` | accounts | Custom user model (email-based) |
| `accounts_apikey` | accounts | API keys for agent ingestion (Argon2id hashed) |
|| `rigs_rig` | rigs | Rig inventory (uuid PK, owner FK, status, last_seen, name, latest_errors_json) |
|| `rigs_rigtag` | rigs | Tags (name, color) |
|| `rigs_rig_tags` | rigs | M2M through table |
|| `metrics_metricsnapshot` | metrics_app | Per-heartbeat metrics (cpu, memory, status fields inline; motherboard/software as JSON; error_count) |
|| `metrics_gpumetric` | metrics_app | Per-GPU metrics (temp, util, mem, power, fan, pcie, core_clock, mem_clock; FK to snapshot) |
|| `metrics_gpu_process` | metrics_app | Per-GPU-process metrics (gpu_index, pid, name, type, mem; latest snapshot only) |
|| `metrics_storagemetric` | metrics_app | Per-disk metrics (capacity, usage%, temp, SMART health) |
|| `metrics_networkmetric` | metrics_app | Per-interface metrics (rx/tx bytes, rx/tx deltas, speed, errors) |
|| `metrics_dockercontainermetric` | metrics_app | Per-container metrics (name, image, status, restarts, cpu%, memory) |
|| `metrics_latestsnapshot` | metrics_app | Denormalized latest snapshot per rig (fast dashboard loading) |
|| `metrics_rig_status_event` | metrics_app | Rig status transition log (online/stale/offline with timestamps) |
|| `metrics_ai_process` | metrics_app | Per-process GPU/CPU usage tracking for AI workloads |
|| `audit_auditlog` | audit | Immutable audit trail |

### 6.1b Management Commands

| Command | Purpose | Schedule |
|---|---|---|
| `update_rig_status` | Updates rig online/stale/offline status based on last_seen | Every 2 min (cron) |
| `compact_data` | Aggregates old metric data into 1-hour buckets | Daily at 3 AM (cron) |
| `cleanup_old_data` | Deletes data older than 31 days in batches of 10,000 | Daily at 3 AM (cron, after compact) |
| `backfill_historical_data` | Creates test data by repeating recent data with shifted timestamps | Manual (testing only) |

### 6.2 Key Constraints

| Table | Constraint |
|-------|------------|
| `metrics_gpumetric` | `UNIQUE(rig_uuid, timestamp, gpu_index)` |
| `metrics_gpu_process` | `UNIQUE(rig_uuid, timestamp, gpu_index, pid)` |
| `metrics_storagemetric` | `UNIQUE(rig_uuid, timestamp, device)` |
| `metrics_networkmetric` | `UNIQUE(rig_uuid, timestamp, interface)` |
| `metrics_dockercontainermetric` | `UNIQUE(rig_uuid, timestamp, name)` |
| `metrics_metricsnapshot` | `UNIQUE(rig_uuid, schema_version, timestamp)` |
| `metrics_ai_process` | `UNIQUE(rig_uuid, timestamp, process_name)` |

### 6.3 Metric Field Name Mapping

The `ChartDataView` uses a name-mapping dict because chart-facing metric names differ from model field names.
Similar mappings exist for other models (StorageMetric, NetworkMetric, etc.) and are handled via query parameters
(multi_gpu, multi_disk, multi_iface, etc.) and special handling for JSON fields and aggregated metrics.

|| Chart Metric (URL param) | GPUMetric Model Field |
|--------------------------|----------------------|
| `gpu_temp_c`             | `gpu_temp_c`         |
| `gpu_util_pct`           | `gpu_util_pct`       |
| `gpu_mem_used_mb`        | `mem_used_mb`        |
| `gpu_mem_total_mb`       | `mem_total_mb`       |
| `gpu_mem_util_pct`       | `mem_util_pct`       |
| `gpu_power_w`            | `power_draw_w`       |
| `gpu_power_limit_w`      | `power_limit_w`      |
| `gpu_fan_pct`            | `fan_speed_pct`      |

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
| Agent payload (Linux) | `/var/log/monitoring-agent/payload.json` | Latest full JSON payload (overwritten each run) |
| Agent payload (Windows) | `./logs/payload.json` (alongside agent) | Latest full JSON payload (overwritten each run) |
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

### 8.5 Data Retention

The platform uses **tiered compaction** to manage long-term storage growth. Without retention, 1,000 rigs would accumulate ~146 GB/month. With compaction: ~7 GB/month (95% savings).

#### Retention Tiers

| Tier | Age | Bucket | Rows/Day/Rig | Savings |
|---|---|---|---|---|
| Raw | 0-1 day | 1-minute | 1,440 | — |
| Compacted | 1-31 days | 1-hour | 24 | 60× |
| Deleted | 31+ days | — | 0 | 100× |

#### Management Commands

Two Django management commands handle retention:

**`compact_data`** — Aggregates old data into larger time buckets:
- Single phase: data > 1 day → 1-hour buckets
- Aggregation: AVG (temperature, utilization, power), SUM (network bytes, error_count), LAST (model names, UUIDs)
- Parent table (`metrics_metricsnapshot`) compacted first; child tables after
- FK-safe: parent rows referenced by children are excluded from compaction

**`cleanup_old_data`** — Deletes data older than N days (default: 31):
- Processes tables in dependency order (children first, parent last)
- Batch deletion (10,000 rows) to avoid long table locks
- Handles non-standard primary keys (`metrics_latest_snapshot` uses `rig_uuid`)

#### Schedule

Both commands run sequentially via `data_retention.sh` wrapper, called by cron at 3 AM:

```bash
# /etc/cron.d/monitoring-data-cleanup
0 3 * * * qrv bash /opt/gpu_monitor/deploy/data_retention.sh >> /var/log/monitoring-agent/cleanup-cron.log 2>&1
```

#### Verification

```bash
cd /opt/gpu_monitor
source venv/bin/activate && set -a && source .env && set +a
python manage.py compact_data --dry-run --verbose
python manage.py cleanup_old_data --dry-run --days=31 --verbose
```

#### Log Location

Retention logs: `/var/log/monitoring-agent/cleanup-cron.log`

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

---

## 10. Performance & Scaling

### 10.1 Payload Size & Network Bandwidth

The agent collects extensive telemetry but the JSON structure is optimized for density.

| Payload Section | Uncompressed (Est.) | Compressed gzip (Est.) |
|-----------------|--------------------|-----------------------|
| Headers & envelope (UUID, timestamps, schema) | ~200 B | ~150 B |
| Inventory (CPU, mobo, GPU specs) | ~2.5 KB | ~600 B |
| Metrics (utilizations, temps, GPU arrays) | ~1.5 KB | ~400 B |
| Software & Docker | ~1.5 KB | ~450 B |
| Errors (latest, deduplicated) | ~1.0 KB | ~300 B |
| **Total per payload** | **~6.7 KB** | **~1.9 KB** |

**Design note:** The agent does NOT compress payloads. Django DRF does not auto-decompress gzip request bodies, so compression was disabled to avoid `JSON parse error - 'utf-8' codec can't decode byte 0x8b` errors on the server.

**Network calculations:**
- Average load: 1,000 rigs / 60s = **16.67 RPS**
- Burst load (3x cron clustering): **~50 RPS peak**
- Peak bandwidth: 50 RPS × 6.7 KB = **335 KB/s (2.68 Mbps)** uncompressed
- A standard 1 Gbps VPS uplink uses < 0.3% of capacity

### 10.2 Write Throughput & Database IOPS

Each ingest performs multiple database operations:

| Target Table | Operation | Purpose |
|-------------|-----------|---------|
| `rigs_rig` | UPDATE | Bump last_seen, set status=ONLINE |
| `metrics_metricsnapshot` | UPSERT | Per-heartbeat metrics (cpu, memory inline) |
| `metrics_gpumetric` | UPSERT | Per-GPU metrics (1 row per GPU) |
| `metrics_storagemetric` | UPSERT | Per-disk metrics |
| `metrics_networkmetric` | UPSERT | Per-interface metrics |
| `metrics_dockercontainermetric` | UPSERT | Per-container metrics |
| `metrics_errorevent` | UPSERT | Deduplicated errors |
| **Total** | **~7-12 writes** | Depending on GPU/disk/container count |

**Peak throughput:**
- 50 RPS × 10 writes = **500 writes/sec** at burst
- PostgreSQL on NVMe sustains 2,000-5,000+ writes/sec
- **Utilization: ~10-25% of peak DB capacity** — well within safety margin

### 10.3 Query Performance Budgets

| Query | Frequency | Optimization | Budget |
|-------|-----------|-------------|--------|
| `IngestView` upsert | Every 60s/rig | `update_or_create` + `unique_together` | < 200 ms |
| `rig_list` fleet table | Every 30s/browser | `prefetch_related('tags')`, indexed `order_by('name')` | < 100 ms |
| `htmx_metrics` live cards | Every 30s/browser | Single-row lookup from `LatestSnapshot` + `GPUMetric` | < 100 ms |
|| `ChartDataView` per chart | On demand (↻) | Time-range filter, SQL aggregation (TruncHour + Avg/Sum) | < 200 ms |
| `update_rig_status` | Every 2 min | Bulk `update()`, no per-row queries | < 1 s |

**Concurrency:** 10 dashboard users × 3 pages/min = ~0.5 QPS read load — statistically insignificant vs. agent write load.

### 10.4 Resource Sizing

| Resource | Minimum | Recommended | Justification |
|----------|---------|-------------|---------------|
| vCPU | 4 | 4-8 | Gunicorn: (2 × cores) + 1 workers |
| RAM | 16 GB | 16-32 GB | PG shared_buffers ~4-6 GB + Gunicorn ~1.3 GB + OS ~1.5 GB |
| Storage | 250 GB NVMe | 500 GB+ NVMe | NVMe mandatory for write IOPS. ~5 GB/day growth. |
| Network | 100 Mbps | 1 Gbps | Peak < 3 Mbps. Egress for 10 users < 5 Mbps. |

**Storage IOPS:** 500 writes/sec burst. Standard SATA SSDs bottleneck during vacuum/compression. NVMe is a strict requirement.

### 10.5 Scaling Path (Beyond 1,000 Rigs)

| Fleet Size | Architectural Change | Impact |
|-----------|---------------------|--------|
| 1,000 | Single VPS (current) | — |
| 2,500 | Add PostgreSQL read replica | Route dashboard queries to replica |
| 5,000 | Decouple HTTP from DB: Nginx → Redis/RabbitMQ → Celery workers → batch insert | Async ingestion |
| 5,000+ | Edge proxies + sharding by rig_uuid | Distributed ingestion |

**v1 readiness:** The Django app is structured so the metrics app can be extracted into a microservice and `IngestView` can be swapped from direct DB write to queue publisher without altering the agent contract.

---

## 11. Testing Strategy

Tests are organized in a pragmatic pyramid: unit tests (mocked hardware) → integration tests (real DB) → E2E (HTMX browser flows). Given the system's reliance on external hardware states, fault-injection at the unit level is prioritized.

### 11.1 Unit Testing (Component Isolation)

**Agent tests** (`pytest + unittest.mock`) enforce partial failure tolerance — the agent must never crash due to missing hardware:

| Test Scenario | Mocking Strategy | Expected Assertion |
|--------------|-----------------|-------------------|
| Happy path | Mock psutil, pynvml, subprocess to return valid data | Payload matches JSON Schema v1.0, all fields populated |
| GPU driver missing | `pynvml.nvmlInit()` raises `NVMLError_DriverNotLoaded` | Agent logs warning, `metrics.gpus` is `[]`, payload still sent |
| SMART fallback | `subprocess.run("smartctl")` raises `CalledProcessError` | `storage[*].smart_health` is null, agent does not abort |
| Network timeout | `requests.post()` raises `ConnectionError` | Retry with exponential backoff (1s, 2s, 4s) verified via mocked `time.sleep` |
| Hard timeout | Mock collector `time.sleep(60)` | `signal.alarm(45)` triggers `TimeoutError`, agent exits cleanly |

**Server tests** (`pytest-django`) focus on DRF serializers, RBAC, and background logic:

| Test Scenario | Target Component | Expected Assertion |
|--------------|-----------------|-------------------|
| Serializer validation | `IngestSerializer` | Rejects missing `rig_uuid` or invalid `schema_version`; accepts unknown extra fields (forward compatibility) |
| Ownership enforcement | `rig_list`, `rig_detail` | User A requesting User B's rig receives 404 (prevents enumeration) |
| `update_rig_status` | Management command | Rigs with `last_seen > 10 min` are strictly marked Offline |
| API key hashing | `ApiKey` model | Saving stores Argon2id hash; plaintext never written to DB |

### 11.2 Integration Testing (Pipeline & Database)

Integration tests verify Django ORM + DRF + PostgreSQL work together.

**Idempotency test** (most critical):

```python
def test_duplicate_payload_is_ignored(api_client, valid_payload):
    # First submission → 200 OK
    r1 = api_client.post('/api/v1/ingest/', valid_payload, format='json')
    assert r1.status_code == 200
    assert MetricSnapshot.objects.count() == 1

    # Exact same payload (simulates agent retry) → 202 Accepted
    r2 = api_client.post('/api/v1/ingest/', valid_payload, format='json')
    assert r2.status_code == 202
    assert MetricSnapshot.objects.count() == 1  # DB unchanged
```

**Ownership test:**

```python
def test_cross_user_rig_access_denied(api_client, user_a, user_b_rig):
    api_client.force_authenticate(user_a)
    response = api_client.get(f'/dashboard/rigs/{user_b_rig.uuid}/')
    assert response.status_code == 404
```

### 11.3 Manual Verification Checklist

After every deployment:

- [ ] Health endpoint: `curl http://localhost/api/v1/health/` → `{"status": "healthy"}`
- [ ] Agent appears on dashboard within 2 minutes
- [ ] Fleet table refreshes every 30s — clock updates
- [ ] Rig detail metrics refresh every 30s — clock updates
- [ ] Rig status transitions: Online → Stale (2 min) → Offline (10 min)
- [ ] Historical charts load + ↻ button refreshes
- [ ] Rig rename works
- [ ] API key creation/revocation work
- [ ] No `hx-swap="outerHTML"` except rename form

### 11.4 Load Testing Approach

```bash
# Install Locust: pip install locust
# Target: 1000 rigs × 1 req/min = ~17 RPS sustained, ~50 RPS burst
# Monitor: Gunicorn error log, DB connection pool, response times
```

### 11.5 Contract Testing for Schema Evolution

When `schema_version` changes:
1. Add new `IngestSerializerV2` alongside V1
2. `SERIALIZER_MAP = {"1.0": IngestSerializerV1, "2.0": IngestSerializerV2}`
3. Old agents continue sending v1.0 → routed to V1 serializer
4. New agents send v2.0 → routed to V2 serializer
5. Deprecation lifecycle: Day 0 (deploy) → Day 30 (recommend upgrade) → Day 180 (deprecated) → Day 365 (dropped)

---

## 12. Troubleshooting

### 12.1 Server Issues

| Problem | Diagnosis | Fix |
|---------|-----------|-----|
| `502 Bad Gateway` | `systemctl status gunicorn` | Check `gunicorn-error.log`; usually import error or DB failure |
| `500 Internal Server Error` | Check `gunicorn-error.log` | Verify `.env` exists with correct DB credentials |
| Database connection refused | `sudo -u postgres psql -c "SELECT 1"` | `systemctl restart postgresql` |
| `PermissionError` in logs | New file has restrictive permissions | `sudo chmod -R 644 /opt/gpu_monitor/templates/` |
| Dashboard shows 500 after update | Template not readable by Gunicorn | `sudo chmod 644 /opt/gpu_monitor/templates/dashboard/*.html` |
| HTMX polling stops after page load | `hx-swap="outerHTML"` destroys wrapper div | Change to `hx-swap="innerHTML"` |
| Clock never updates on Fleet Overview | JS only in `rig_detail.html`, not `base.html` | Move `htmx:afterSwap` listener to `base.html` |
| Clock shows but data is stale | View may be caching — check DB directly | Verify view queries DB on every request |
| "Refreshed @" shows but metrics wrong | Serializer field name mismatch | Check `GPU_METRICS` mapping dict in `ChartDataView` |
| Storage shows duplicate disks | Missing deduplication in view | Add `seen_devices` set in `htmx_metrics` view |
| `ALLOWED_HOSTS` error | Host not in allowed list | Set `DJANGO_ALLOWED_HOSTS=*` in `.env` |
| `CSRF verification failed` | Missing `@csrf_exempt` on `IngestView` | Add `@method_decorator(csrf_exempt, name='dispatch')` |

### 12.2 Agent Issues

| Problem | Diagnosis | Fix |
|---------|-----------|-----|
| `401 Unauthorized` | API key mismatch | Regenerate key on dashboard, update `config.yaml` |
| `Connection refused` | Server firewall or Nginx down | `curl -v http://localhost/api/v1/health/` from the rig |
| `JSON parse error` in server log | Payload is gzip-compressed | Agent must NOT gzip (DRF doesn't decompress requests) |
| `JSON parse error - utf-8 codec` | Windows encoding issue | Agent uses `encoding='utf-8', errors='replace'` |
| Agent hangs / overlaps | Stale lock file | `rm -f /var/lock/monitoring-agent.lock` |
| `PermissionError: config.yaml` | File owned by root | `chown monitoring-agent:monitoring-agent /etc/monitoring-agent/config.yaml` |
| GPU metrics empty | `pynvml` not installed | `pip install pynvml` in agent venv |
| `smartctl: command not found` | Disk tools not installed | `apt install smartmontools nvme-cli` |

### 12.3 Cron Job Issues

| Problem | Diagnosis | Fix |
|---------|-----------|-----|
| Cron job never runs | `source` doesn't work in `/bin/sh` | Use wrapper script with `bash` explicitly |
|| `update_rig_status` has no effect | `.env` not loaded in wrapper | Wrapper must `set -a && source .env && set +a` **before** calling `python manage.py`. Django reads DB credentials from `os.environ` — if `.env` is not sourced, the DB password is empty and authentication fails |
| Cron log shows `password authentication failed` | `.env` not sourced, or wrong user | Ensure wrapper sources `.env` and cron runs as a user with read access to `.env` |
| Cron log is empty | Cron daemon not running | `sudo systemctl restart cron` |
| Crontab syntax error | Inline `source` in cron | Replace with `bash /opt/gpu_monitor/deploy/update_rig_status.sh` |

### 12.4 Service Status & Logs

```bash
# All critical services
systemctl status gunicorn postgresql nginx cron

# View recent logs
journalctl -u gunicorn --since "1 hour ago" --no-pager
tail -50 /opt/gpu_monitor/logs/gunicorn-error.log
tail -50 /opt/gpu_monitor/logs/gunicorn-access.log
tail -50 /opt/gpu_monitor/logs/app.log
tail -50 /var/log/monitoring-agent/agent.log

# Verify HTMX is polling (watch access log for repeated requests)
tail -f /opt/gpu_monitor/logs/gunicorn-access.log | grep "dashboard/rigs"

# Database console
sudo -u postgres psql gpu_monitor
```

---

## 13. File Locations Reference

### 13.1 Server (`/opt/gpu_monitor/`)

| Path | Purpose |
|------|---------|
| `gpu_monitor/` | Django project (`settings.py`, `urls.py`, `wsgi.py`) |
| `accounts/` | User/auth app (models, views, API key middleware) |
| `rigs/` | Rig inventory app (models, status management command) |
| `metrics_app/` | Ingestion API (models, serializers, views) |
| `dashboard/` | HTMX dashboard (views, URLs) |
| `dashboard/templatetags/gpu_filters.py` | GPU model name cleanup filters |
| `audit/` | Audit logging (models, middleware) |
| `templates/base.html` | Base layout (HTMX, Tailwind, Chart.js, clock JS) |
| `templates/dashboard/rig_list.html` | Fleet Overview page |
| `templates/dashboard/rig_detail.html` | Rig detail page (tabs, charts, clocks) |
| `templates/dashboard/_rig_table.html` | Fleet table partial (HTMX-swapped) |
| `templates/dashboard/_metrics_cards.html` | Live metrics cards partial (HTMX-swapped) |
| `templates/dashboard/_rig_status_badge.html` | Status badge partial (HTMX-swapped) |
| `templates/dashboard/_rig_name.html` | Rig name partial (HTMX-swapped on rename) |
| `gpu_monitor/deploy/` | Nginx config, Gunicorn systemd unit, install scripts, cron wrappers |
| `gpu_monitor/deploy/update_rig_status.sh` | Cron wrapper for rig status updates |
| `scripts/sync_to_opt.sh` | Workspace → /opt deployment script |
| `.env` | Environment variables (mode 0600) |
| `venv/` | Python virtual environment |
| `logs/` | Application logs |
| `staticfiles/` | Collected static files served by Nginx |
| `manage.py` | Django management command |

### 13.2 Agent

| Path | Purpose |
|------|---------|
| `/opt/monitoring-agent/run.py` | Linux agent script |
| `/opt/monitoring-agent/venv/` | Agent Python virtual environment |
| `/opt/agent_windows/run.py` | Windows agent script |
| `/etc/monitoring-agent/config.yaml` | Agent configuration (mode 0600) |
| `/var/log/monitoring-agent/agent.log` | Agent logs (JSON, rotated 10 MB × 3) |
| `/var/log/monitoring-agent/payload.json` | Latest full JSON payload sent to server (overwritten each run) |
| `/var/log/monitoring-agent/cron.log` | Cron output log |

### 13.3 System

| Path | Purpose |
|------|---------|
| `/etc/nginx/sites-available/gpu_monitor` | Nginx site configuration |
| `/etc/systemd/system/gunicorn.service` | Gunicorn systemd unit |
| `/etc/cron.d/monitoring-agent` | Agent cron job (every 60s) |
| `/etc/cron.d/rig-status` | Rig status update cron (every 2 min) |
| `/etc/logrotate.d/gpu-monitor` | Log rotation configuration |
|| `/etc/sudoers.d/monitoring-agent` | Agent sudo permissions (smartctl, journalctl) |

---

## 14. Appendices

### A. Full JSON Schema Definitions (Agent Payload)

**Current: v1.1** (see changelog below)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "GPU Rig Monitoring Agent Payload v1.1",
  "type": "object",
  "required": ["rig_uuid", "schema_version", "timestamp", "metrics"],
  "properties": {
    "rig_uuid": { "type": "string", "format": "uuid" },
    "rig_name": { "type": "string", "maxLength": 128 },
    "schema_version": { "type": "string", "enum": ["1.0", "1.1"] },
    "agent_version": { "type": "string" },
    "timestamp": { "type": "string", "format": "date-time" },
    "metrics": {
      "type": "object",
      "properties": {
        "cpu": {
          "type": "object",
          "properties": {
            "model": { "type": "string" },
            "physical_cores": { "type": "integer" },
            "logical_cores": { "type": "integer" },
            "load_avg": { "type": "array", "items": { "type": "number" } },
            "utilization_pct": { "type": "number" },
            "temp_c": { "type": ["number", "null"] }
          }
        },
        "memory": {
          "type": "object",
          "properties": {
            "total_bytes": { "type": "integer" },
            "used_bytes": { "type": "integer" },
            "free_bytes": { "type": "integer" },
            "cached_bytes": { "type": "integer" },
            "swap_used_bytes": { "type": "integer" },
            "swap_total_bytes": { "type": "integer" }
          }
        },
        "storage": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "device": { "type": "string" },
              "mountpoint": { "type": "string" },
              "fstype": { "type": "string" },
              "capacity_bytes": { "type": "integer" },
              "usage_pct": { "type": "number" },
              "temp_c": { "type": ["number", "null"] },
              "smart_health": { "type": "string" }
            }
          }
        },
        "network": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "interface": { "type": "string" },
              "ipv4": { "type": "string" },
              "rx_bytes": { "type": "integer" },
              "tx_bytes": { "type": "integer" },
              "rx_errors": { "type": "integer" },
              "tx_errors": { "type": "integer" },
              "link_speed_mbps": { "type": "integer" }
            }
          }
        },
        "gpus": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "uuid": { "type": "string" },
              "model": { "type": "string" },
              "mem_total_mb": { "type": "integer" },
              "mem_used_mb": { "type": "integer" },
              "mem_free_mb": { "type": "integer" },
              "mem_util_pct": { "type": "number" },
              "gpu_util_pct": { "type": "number" },
              "temp_c": { "type": ["number", "null"] },
              "fan_speed_pct": { "type": ["number", "null"] },
              "power_draw_w": { "type": ["number", "null"] },
              "power_limit_w": { "type": ["number", "null"] }
            }
          }
        },
        "ai_processes": { "type": "array", "items": { "type": "object" } },
        "docker_containers": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "name": { "type": "string" },
              "image": { "type": "string" },
              "status": { "type": "string" },
              "restart_count": { "type": "integer" }
            }
          }
        }
      }
    },
    "motherboard": {
      "type": "object",
      "properties": {
        "manufacturer": { "type": "string" },
        "model": { "type": "string" },
        "bios_version": { "type": "string" }
      }
    },
    "software": {
      "type": "object",
      "properties": {
        "hostname": { "type": "string" },
        "os_distro": { "type": "string" },
        "kernel": { "type": "string" },
        "uptime_s": { "type": "integer" },
        "nvidia_driver": { "type": "string" },
        "docker_version": { "type": "string" }
      }
    },
    "errors": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "source": { "type": "string" },
          "message": { "type": "string" },
          "timestamp": { "type": "string" }
        }
      }
    }
  }
}
```

**Schema 1.0 → 1.1 changelog:**
- Removed `inventory` top-level key (was a 100% duplicate of `metrics`)
- Added `motherboard` as a top-level key (previously nested inside `inventory`)
- `schema_version` enum now accepts both `"1.0"` and `"1.1"` (backward compatible)
- All metric fields preserved — no data loss

### B. Endpoint Catalog (Summary)

| Method | Path | Auth | Purpose | HTMX Clock |
|--------|------|------|---------|------------|
| POST | `/api/v1/ingest/` | API Key | Telemetry submission | No |
| GET | `/api/v1/health/` | None | Health check | No |
| GET | `/dashboard/rigs/` | Session | Fleet Overview (initial load) | No |
| GET | `/dashboard/rigs/` (HX-Request) | Session | Fleet table partial (30s poll) | `#rig-table-container-clock` |
| GET | `/dashboard/rigs/<uuid>/` | Session | Rig detail page | No |
| GET | `/dashboard/rigs/<uuid>/htmx-metrics/` | Session | Live metrics partial (30s poll) | `#metrics-container-clock` |
| GET | `/dashboard/rigs/<uuid>/htmx-status/` | Session | Status badge partial (15s poll) | `#rig-status-container-clock` |
| POST | `/dashboard/rigs/<uuid>/rename/` | Session | Rename rig | No |
| GET | `/api/v1/rigs/<uuid>/chart-data/` | Session | Historical chart JSON | No (↻ button) |

### C. Sample systemd Unit (Gunicorn)

```ini
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
```

### D. Cron Jobs Summary

```bash
# /etc/cron.d/monitoring-agent — Linux agent, every 60s
* * * * * monitoring-agent flock -n /var/lock/monitoring-agent.lock \
    /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/run.py \
    >> /var/log/monitoring-agent/cron.log 2>&1

# /etc/cron.d/rig-status — Rig status update, every 2 min
*/2 * * * * root bash /opt/gpu_monitor/deploy/update_rig_status.sh
```

### E. Glossary

| Term | Definition |
|------|-----------|
| **Rig** | A single monitored machine (physical or VM) running the agent |
| **Fleet** | The collection of all rigs visible in the dashboard |
| **Agent** | The Python script running on each rig that collects and sends metrics |
| **Ingest** | The act of receiving a telemetry payload from an agent |
| **HTMX** | Hypermedia library for server-rendered HTML with AJAX swaps |
| **DRF** | Django REST Framework — handles API authentication and serialization |
| **Idempotency** | Sending the same payload twice produces the same result (no duplicates) |
| **API Key** | 32-byte hex string used by agents for authentication (Argon2id hashed) |
| **Session** | Browser cookie-based authentication for dashboard users |
| **SOX** | Socket-level timeout for network operations |
| **flock** | File lock preventing overlapping agent runs |
| **Stale** | Rig status — not reported in 2-10 minutes |
| **Offline** | Rig status — not reported in 10+ minutes |

---
