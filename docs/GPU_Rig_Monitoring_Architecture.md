# GPU Rig Monitoring Platform — Architecture Document

**Version:** 1.5
**Status:** Implemented — Living Architecture Reference
**Last Updated:** 2026-06-17

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

**Measured storage per rig (100% uptime):** ~15.7 MB/day
- At 50% uptime: ~7.9 MB/day
- 31-day retention with tiered compaction: ~23.6 MB/rig (~72 GB total for 1,000 rigs)
- Without compaction: ~487 GB for 1,000 rigs/month

**Data retention:** 31 days (matches 30-day max chart range + 1 day safety margin)
- 0-1 day: raw per-minute data
- 1-31 days: compacted to 1-hour buckets
- 31+ days: deleted

**Note:** Earlier projections estimated ~4.7 MB/day/rig. Actual measurements from 100 rigs over 10 days show ~15.7 MB/day/rig (~3.3x higher) due to larger row sizes from JSON fields (motherboard_json, software_json, cpu_load_avg_json) and higher-than-expected Docker container metric volume.

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

```text
Cron → Agent collects metrics → JSON payload → POST /api/v1/ingest/
  → Nginx (rate limit: 2r/min per rig_uuid burst=5, payload size check)
  → DRF APIKeyAuthentication (X-API-Key header → Argon2id hash comparison)
  → DRF throttle (per-rig rate limit via X-Rig-UUID header, 2/min per rig)
  → Timestamp sanity check (reject if >5 min future or >1 hour past)
  → IngestSerializer validation (schema version 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, or 1.8)
  → process_ingest() → DB upsert (MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, LatestDockerContainer, RigStatusEvent, LatestSnapshot)
  → StorageMetric: capacity, usage%, temp, SMART, read/write bytes, read/write IOPS, busy_time_ms, utilization%
  → LatestSnapshot: 11 storage JSON arrays (devices, fstypes, mountpoints, capacities, usage%, temps, smart, deltas, totals), 3 process fields (top_cpu_processes_json, top_mem_processes_json, process_count)
  → Rig.latest_errors_json updated with latest error text
  → Rig.last_seen and Rig.status updated to ONLINE
  → Response: 200 (new) or 202 (duplicate/idempotent)

All other endpoints (dashboard, login, static):
  → Nginx general rate limit: 30r/s per IP (burst=20 for pages, burst=50 for static)
  → Django per-user rate limit: 60 req/min (rig_list, rig_detail), 120 req/min (htmx polling)
  → Anonymous: IP-based rate limit via Django decorator

Rate limiting design:
  - Ingest: per-rig-uuid ONLY (no API key shared bucket)
  - Each rig gets its own rate limit bucket regardless of source IP or API key
  - No shared buckets that could block server rooms with many rigs
  - Dashboard: per-user for authenticated, per-IP for anonymous
```

### 2.3 Key Files

| File | Purpose |
|------|---------|
| `agent/run.py` | Linux agent (~517 lines) |
| `agent_windows/run.py` | Windows agent (~916 lines) |
| `metrics_app/views.py` | IngestView, HealthView, ChartDataView, RigMetricsView |
| `metrics_app/serializers.py` | IngestSerializer, process_ingest() |
|| `metrics_app/models.py` | MetricSnapshot, GPUMetric, GPUProcessMetric, StorageMetric, NetworkMetric, LatestDockerContainer, LatestSnapshot (with GPU JSON fields), RigStatusEvent |
| `dashboard/views.py` | index_view (root → dashboard/login redirect), rig_list, rig_detail, htmx_metrics, htmx_rig_status, rig_rename |
| `dashboard/templatetags/gpu_filters.py` | gpu_model_name, gpu_model_short, gpu_compact_summary_json, gpu_temp_cell_json, gpu_util_cell_json, gpu_fan_cell_json, time_since, last_seen_short filters |
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
collection_timeout_s: 30  # Hard timeout per collection cycle (default in code)
jitter_s: 0-25            # Random delay before collection to spread load
retry_attempts: 3         # Exponential backoff: 1s → 2s → 4s
debug_mode: false         # Verbose logging
```

### 3.3 Payload Schema (v1.7)

```json
{
  "rig_uuid": "UUIDv4",
  "rig_name": "my-server",
  "schema_version": "1.7",
  "agent_version": "1.5.9",
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
        "smart_health": "",
        "read_bytes": 37688539648,
        "write_bytes": 156538570752,
        "read_iops": 3309393,
        "write_iops": 6397960,
        "busy_time_ms": null
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
        "power_limit_w": 170.0,
        "pcie_current_gen": 1,
        "pcie_max_gen": 3,
        "pcie_current_width": 16,
        "pcie_max_width": 16,
        "gpu_core_clock_mhz": 210,
        "gpu_mem_clock_mhz": 405
      }
    ],
    "gpu_processes": [
      {
        "gpu_index": 0,
        "pid": 2247,
        "type": "G",
        "name": "/usr/lib/xorg/Xorg",
        "gpu_mem_mb": 6
      }
    ],
    "docker_containers": [
      {
        "name": "ollama",
        "image": "ollama/ollama:latest",
        "status": "running",
        "container_id": "a1b2c3d4e5f6",
        "created": "2026-06-01T12:00:00Z",
        "status_text": "Up 2 days"
      }
    ],
    "top_processes": {
      "by_cpu": [
        {"pid": 3502, "name": "firefox", "cpu_pct": 54.5, "mem_pct": 8.5,
         "username": "qrv", "cmdline": "/usr/lib/firefox/firefox", "status": "S"}
      ],
      "by_mem": [
        {"pid": 1688, "name": "python", "cpu_pct": 9.5, "mem_pct": 8.8,
         "username": "qrv", "cmdline": "/home/qrv/.hermes/hermes-agent/venv/bin/python", "status": "S"}
      ],
      "total_count": 371
    }
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

**Changelog from schema 1.7 → 1.8:**
- Added `top_processes` object to `metrics` section with `by_cpu`, `by_mem`, and `total_count`
- `by_cpu`: top 20 processes sorted by CPU% descending
- `by_mem`: top 20 processes sorted by memory% descending
- Each process entry: `pid`, `name`, `cpu_pct`, `mem_pct`, `username`, `cmdline`, `status`
- Server stores in LatestSnapshot: `top_cpu_processes_json`, `top_mem_processes_json`, `process_count`
- Live Metrics adds "Top Processes" card with two side-by-side tables (By CPU / By Memory)
- Agent uses psutil two-pass approach: baseline → sleep 0.5s → measure

**Changelog from schema 1.6 → 1.7:**
- Added disk I/O metrics to `storage[]` objects: `read_bytes`, `write_bytes`, `read_iops`, `write_iops`, `busy_time_ms`
- `read_bytes`/`write_bytes`: cumulative bytes since boot (like network rx/tx_bytes)
- `read_iops`/`write_iops`: cumulative operation counts since boot
- `busy_time_ms`: cumulative milliseconds disk spent doing I/O (Linux only; Windows returns null)
- Server computes deltas during ingest by comparing with previous reading for each disk
- Server derives `utilization_pct` from `busy_time_delta / sample_interval * 100`
- 5 new chart metrics: `disk_read_bytes_delta`, `disk_write_bytes_delta`, `disk_read_iops_delta`, `disk_write_iops_delta`, `disk_utilization_pct`
- Live Metrics storage card shows: Total Read/Write (cumulative), Since last update (delta), IOPS, Utilization%
- Fleet Overview adds Disk Util [%] column (max across all disks, color-coded)

**Changelog from schema 1.5 → 1.6:**
- Added `gpu_core_clock_mhz` and `gpu_mem_clock_mhz` to GPU metrics (pynvml `NVML_CLOCK_GRAPHICS` and `NVML_CLOCK_MEM`)
- Added GPU Core Clock and GPU Memory Clock charts (multi-GPU)
- Added clock display to Live Metrics GPU card

**Changelog from schema 1.4 → 1.5:**
- Added `container_id` and `status_text` to docker container metrics
- Docker Live Metrics now shows table with: Status, Name, Image, Container ID, Uptime
- Containers sorted by uptime descending (longest running first)

### 3.4 Transport

- **Compression:** None (Django DRF does not auto-decompress gzip request bodies)
- **Idempotency:** Same `rig_uuid + schema_version + timestamp` → 202 Accepted (not 200)
- **Retry:** Exponential backoff with jitter, max 3 attempts, 30s hard timeout

### 3.5 Two Agents

| Agent | File | Version | Schema | Platform | Scheduling |
|-------|------|---------|--------|----------|------------|
|| Linux | `agent/run.py` | 1.5.11 | 1.8 | Any Linux, VMware NAT | `cron` every 60s with `flock` |
|| Windows | `agent_windows/run.py` | 1.6.12-win | 1.8 | Windows 10/11 | Task Scheduler (1 min) with `pythonw.exe` (hidden window) |

**Versioning rules:**
- `agent_version` (e.g. `1.1.0`): incremented for agent-side changes (collectors, payload format, bug fixes). Format: `MAJOR.MINOR.PATCH`.
- `schema_version` (e.g. `1.1`): incremented only when the payload structure changes in a way that affects the server's serialization/storage. Format: `MAJOR.MINOR`.
- Schema versions 1.0 through 1.6 are supported (backward compatible via `validate_schema_version` in `IngestSerializer`).
- When schema versions change, the `validate_schema_version` method in `IngestSerializer` is updated to accept the new version. The same serializer handles all supported versions.
- See §11.5 for the contract testing strategy.

---

## 4. Server Architecture

### 4.1 Django Apps

| App | Models | Key Views |
|-----|--------|-----------|
| `gpu_monitor` | — | Settings, URL routing, WSGI |
| `accounts` | User, ApiKey | Login, logout, API key management |
| `rigs` | Rig, RigTag | `update_rig_status` management command |
|| `metrics_app` | MetricSnapshot, GPUMetric, GPUProcessMetric, StorageMetric, NetworkMetric, LatestDockerContainer, LatestSnapshot, RigStatusEvent | IngestView, HealthView, ChartDataView, RigMetricsView |
| `dashboard` | — | rig_list, rig_detail, htmx_metrics, htmx_rig_status, rig_rename |
| `audit` | AuditLog | Middleware-based request logging |
| `dashboard/templatetags` | — | gpu_model_name, gpu_model_short, gpu_compact_summary_json, gpu_temp_cell_json, gpu_util_cell_json, gpu_fan_cell_json, time_since, last_seen_short filters |

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
  → Nginx rate limit: 2r/min per rig_uuid (burst=5)
  → DRF throttle (per-rig rate limit via X-Rig-UUID header, 2/min per rig)
  → IngestSerializer validation (schema version 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, or 1.6)
  → process_ingest() in transaction.atomic():
      - Upsert MetricSnapshot (cpu, memory, status fields; motherboard/software as JSON; error_count)
      - Upsert GPUMetric per GPU (gpu_index = 0, 1, ...)
      - Delete + recreate GPUProcessMetric per process (latest snapshot only)
      - Upsert StorageMetric per disk (with path-normalized dedup)
      - Upsert NetworkMetric per interface (with rx/tx delta calculation)
      - Delete + recreate LatestDockerContainer per container (container_id, name, image, status, created, status_text — latest snapshot for Live Metrics)
      - Create RigStatusEvent on status transition (e.g. offline→online)
      - Update Rig.latest_errors_json with latest error text from payload
      - Update LatestSnapshot (denormalized cache for fast dashboard loading):
          * CPU: cpu_utilization_pct, cpu_temp_c, mem_used_bytes, mem_total_bytes
          * GPU (JSON arrays): gpu_count, gpu_uuids_json, gpu_models_json, gpu_temps_json, gpu_utils_json, gpu_fans_json, gpu_core_clocks_json, gpu_mem_clocks_json, gpu_mem_used_json, gpu_mem_total_json, gpu_mem_util_pcts_json, gpu_mem_free_json, gpu_power_draws_json, gpu_power_limits_json, gpu_pcie_gen_json, gpu_pcie_max_gen_json, gpu_pcie_width_json, gpu_pcie_max_width_json
          * Storage (JSON arrays): storage_count, storage_devices_json, storage_fstypes_json, storage_mountpoints_json, storage_capacities_json, storage_usage_pcts_json, storage_temps_json, storage_smart_json
          * Network (JSON arrays): network_count, network_interfaces_json, network_ipv4s_json, network_speeds_json, network_rx_bytes_json, network_tx_bytes_json, network_rx_errors_json, network_tx_errors_json
          * Cache invalidation: cache.delete(lsnap_{uuid})
      - Update Rig.last_seen = now(), Rig.status = ONLINE, cache.delete(lsnap_{uuid})
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
| POST | `/api/v1/ingest/` | API Key + X-Rig-UUID header | Telemetry submission (per-rig rate limit via X-Rig-UUID, 2/min, timestamp sanity check) |
| GET | `/api/v1/health/` | None | Health check (DB + active rigs count) |
| GET | `/api/v1/rigs/<uuid>/metrics/` | Session | Latest metrics (used by Chart.js direct fetch) |
| GET | `/api/v1/rigs/<uuid>/chart-data/?metric=X&range=N` | Session | Historical chart data |
| GET | `/dashboard/rigs/` | Session | Fleet Overview (HTMX) |
| GET | `/dashboard/rigs/<uuid>/` | Session | Rig detail page |
| GET | `/dashboard/rigs/<uuid>/htmx-metrics/` | Session | Live metrics partial (30s poll) |
| GET | `/dashboard/rigs/<uuid>/htmx-status/` | Session | Status badge partial (15s poll) |
| POST | `/dashboard/rigs/<uuid>/rename/` | Session | Rename rig |
| POST | `/dashboard/rigs/<uuid>/delete/` | Session | Delete rig and all associated data |
| POST | `/dashboard/rigs/<uuid>/tags/<tag_id>/toggle/` | Session | Toggle tag on/off for a rig |
| GET | `/accounts/login/` | None | Login page |
| GET | `/accounts/register/` | None | Registration page |
| POST | `/accounts/logout/` | Session | Logout |
| GET | `/accounts/profile/` | Session | User profile (view info, change password) |
| GET | `/accounts/api-keys/` | Session | API key management |
| POST | `/accounts/api-keys/create/` | Session | Create new API key |
| POST | `/accounts/api-keys/<key_id>/revoke/` | Session | Revoke API key |
| GET | `/accounts/tags/` | Session | Tag management |
| POST | `/accounts/tags/create/` | Session | Create tag |
| POST | `/accounts/tags/<tag_id>/update/` | Session | Update tag |
| POST | `/accounts/tags/<tag_id>/delete/` | Session | Delete tag |

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
- `LatestSnapshot` (cpu_utilization_pct, mem_used_bytes, cpu_temp_c, gpu_count, gpu_models_json, gpu_temps_json, gpu_utils_json, gpu_fans_json)

**GPU data denormalization:** GPU metrics (model, temp, util, fan) are stored as JSON arrays in `LatestSnapshot` during ingest. This eliminates the need to query the `GPUMetric` timeseries table (2.1M+ rows) for the fleet overview. Each array entry corresponds to one GPU, ordered by `gpu_index`.

**Sorting:** Naturally by `Rig.name` (e.g., rig2 before rig11).

**Columns and data sources:**

| Column | Source | Model Field |
|--------|--------|-------------|
| Rig Name | Rig.name | Clickable link to detail |
| Status | Rig.status | Online/Stale/Offline |
| Last Seen | Rig.last_seen | Short relative time via `last_seen_short` filter (e.g., '5d, 21h', '45m', '20s') |
| Tags | RigTag M2M | Colored pills |
| GPU | LatestSnapshot.gpu_models_json | Compact summary via `gpu_compact_summary_json` filter (e.g., "RTX 3060 ×8", "5080×4 + ...") |
| GPU Temp [°C] | LatestSnapshot.gpu_temps_json | Space-separated color-coded values via `gpu_temp_cell_json` |
| GPU Util [%] | LatestSnapshot.gpu_utils_json | Space-separated color-coded values via `gpu_util_cell_json` |
| GPU Fan [%] | LatestSnapshot.gpu_fans_json | Space-separated color-coded values via `gpu_fan_cell_json` |
|| CPU [%] | LatestSnapshot.cpu_utilization_pct | Percentage |
|| Memory [%] | LatestSnapshot.mem_used_bytes, mem_total_bytes | Used / Total (GB) |
|| Disk Util [%] | LatestSnapshot.storage_utilization_pcts_json | Color-coded max utilization |

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
| Historical charts | User clicks ↻ button | Combined charts: GPU Temperature/Utilization/Memory/Power/Fan Speed (multi-GPU), CPU Utilization/Temperature/Load Average, Memory & Swap (combined, 3 datasets), Disk Usage (multi-disk), Network Traffic (combined RX/TX/Errors, dual Y-axes), Container CPU/Memory (multi-container), Uptime, Error Frequency. Timeframe toggle buttons (24h, 7d, 30d) with dynamic label updates. |

**Historical charts are NOT polled automatically** — they load once when the tab is first opened and refresh only when the user clicks the ↻ button. This avoids expensive time-series queries every 30 seconds.

**Live Metrics data source:** `_fetch_rig_metrics()` reads from `LatestSnapshot` (single row per rig) for GPU, storage, and network data. This eliminates all timeseries `DISTINCT ON` queries for the Live Metrics poll. Only Docker container metrics and GPU process data still query timeseries tables (small, fast queries).

### 5.4 Tab Layout

The rig detail page has three tabs:

1. **Live Metrics** — cards with CPU%, memory bar, GPU model/index/temp/util/fan/power/PCIe/vRAM/clocks (all from LatestSnapshot), GPU Processes (per-process: name, type badge C/G/C+G, memory), Docker container count with container_id/image/status/created/status_text, storage disks with Total Read/Write (cumulative), Since last update (delta), IOPS, Utilization%, top processes by CPU and memory (PID, name, CPU%, mem%, user), recent errors
2. **Historical Charts** — Combined chart suite: GPU (Temperature, Utilization, Memory, Power, Fan Speed — multi-GPU), CPU (Utilization, Temperature, Load Average), Memory & Swap (combined single chart, 3 datasets), Disk Usage (multi-disk), Network Traffic (combined RX/TX/Errors, dual Y-axes), Container CPU/Memory (multi-container), Uptime, Error Frequency — all implemented as Chart.js charts with multi-series support. Timeframe toggle buttons (24h, 7d, 30d) in the tab header with a ↻ Refresh button.
3. **Errors** — latest system errors from journalctl/Windows Event Log (stored on Rig model, updated in place)

### 5.5 Snapshot-Timeseries Decoupling

The dashboard display (Fleet Overview + Live Metrics) is fully decoupled from timeseries data. This is the single most important architectural decision for dashboard performance.

**Core principle:** Display data (latest values) is stored in `LatestSnapshot` during ingest. Chart data (historical trends) is stored in timeseries tables. These two paths never mix.

#### Snapshot Data Path (Display Only)

`LatestSnapshot` is a single row per rig, updated on every heartbeat via `update_or_create`. It stores the latest value of every metric needed for dashboard display:

| Category | Fields |
|---|---|
| CPU | cpu_model, cpu_physical_cores, cpu_logical_cores, cpu_utilization_pct, cpu_temp_c, cpu_load_avg_json |
| Memory | mem_total_bytes, mem_used_bytes, mem_free_bytes, mem_cached_bytes, swap_total_bytes, swap_used_bytes |
| System | uptime_s, motherboard_json, software_json, agent_version |
||| GPU (×N) | 17 JSON arrays (uuid/model/temp/util/fan/clocks/mem/power/PCIe) |
||| Storage (×N) | 7 JSON arrays (device/fstype/mountpoint/capacity/usage/temp/SMART) |
||| Network (×N) | 7 JSON arrays (interface/IPv4/speed/rx/tx/errors) |
||| Processes | top_cpu_processes_json, top_mem_processes_json, process_count |

**Views using snapshot data:**
- `rig_list` (Fleet Overview): Reads `LatestSnapshot` + `Rig` + `RigTag`. **0 timeseries queries.**
- `htmx_metrics` (Live Metrics): Reads `LatestSnapshot` + `LatestDockerContainer` + `GPUProcessMetric`. **0 timeseries queries for GPU/storage/network.**

#### Timeseries Data Path (Charts Only)

Timeseries tables store every heartbeat's data for historical chart aggregation:

| Table | Purpose | Retention |
|---|---|---|
|| `MetricSnapshot` | CPU, memory, uptime, error_count per heartbeat | 31 days |
|| `GPUMetric` | Per-GPU metrics per heartbeat | 31 days |
|| `StorageMetric` | Per-disk metrics per heartbeat | 31 days |
|| `NetworkMetric` | Per-interface metrics per heartbeat | 31 days |

**Views using timeseries data:**
- `ChartDataView` (Historical Charts): Aggregates timeseries data with `GROUP BY` time bucket (1min for 24h, 1hr for 7d/30d). This is the **only** view that queries timeseries tables.

#### Query Count Comparison

| View | Before | After | Timeseries |
|---|---|---|---|
| Fleet Overview | 2002+ queries | 4 queries | 0 |
| Live Metrics | ~1500ms (3 DISTINCT ON) | <100ms | 0 for GPU/storage/network |
| Historical Charts | Unchanged | Unchanged | All timeseries |

#### Performance Characteristics

- **Fleet Overview:** O(1) per rig — single row lookup from `LatestSnapshot`
- **Live Metrics:** O(1) per rig — single row lookup + small Docker/process queries
- **Historical Charts:** O(time_range) — aggregates timeseries data, unaffected by fleet size per user
- **Scalability:** Display performance is independent of timeseries table size. A rig with 1 day of data loads the same as a rig with 31 days of data.

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
|| `metrics_metricsnapshot` | metrics_app | Per-heartbeat metrics for charts (cpu, memory, uptime, error_count) |
| `metrics_gpumetric` | metrics_app | Per-GPU metrics (temp, util, mem, power, fan, pcie, core_clock, mem_clock; FK to snapshot) |
|| `metrics_gpu_process` | metrics_app | Per-GPU-process metrics (gpu_index, pid, name, type, mem; latest snapshot only) |
||| `metrics_storagemetric` | metrics_app | Per-disk metrics (capacity, usage%, temp, SMART health, read/write bytes, read/write IOPS, busy_time_ms, utilization%; FK to snapshot) |
||| `metrics_networkmetric` | metrics_app | Per-interface metrics (rx/tx bytes, rx/tx deltas, speed, errors) |
||| `metrics_latest_docker_container` | metrics_app | Latest container snapshot (name, container_id, image, status, created, status_text; for Live Metrics) |
|||| `metrics_latestsnapshot` | metrics_app | Denormalized latest snapshot per rig (fast dashboard loading). Single row per rig, updated every heartbeat. Stores all display data: cpu_model, cpu_physical_cores, cpu_logical_cores, cpu_utilization_pct, cpu_temp_c, cpu_load_avg_json, mem_total_bytes, mem_used_bytes, mem_free_bytes, mem_cached_bytes, swap_total_bytes, swap_used_bytes, uptime_s, motherboard_json, software_json, agent_version, 17 GPU JSON arrays, 11 storage JSON arrays, 7 network JSON arrays, 3 process fields (top_cpu_processes_json, top_mem_processes_json, process_count). Total: ~59 fields. |
|| `metrics_rig_status_event` | metrics_app | Rig status transition log (online/stale/offline with timestamps) |
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
| `metrics_latest_docker_container` | `UNIQUE(rig_uuid, name)` |
| `metrics_metricsnapshot` | `UNIQUE(rig_uuid, schema_version, timestamp)` |

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
|| `gpu_mem_util_pct`       | `mem_util_pct`       |
|| `gpu_power_w`            | `power_draw_w`       |
|| `gpu_power_limit_w`      | `power_limit_w`      |
|| `gpu_fan_pct`            | `fan_speed_pct`      |
|| `gpu_core_clock_mhz`     | `gpu_core_clock_mhz` |
|| `gpu_mem_clock_mhz`      | `gpu_mem_clock_mhz`  |

---

## 7. Security Trust Boundaries

| Zone | Components | Enforcement |
|------|-----------|-------------|
| Z1: Fleet (untrusted) | Agents, internet | HTTP (local) or HTTPS, API key auth |
| Z2: Edge | Nginx | Rate limiting, payload size caps |
| Z3: Application | Django, Gunicorn | Session auth, CSRF, ownership checks |
| Z4: Data | PostgreSQL | localhost-only, least-privilege DB user |

**CSRF exemption:** `IngestView` uses `@csrf_exempt` because it authenticates via API key (not session cookie). The agent has no CSRF token.

**Auth settings:**
```
AUTH_USER_MODEL = 'accounts.User'          # Custom user model (email as username)
LOGIN_URL = '/accounts/login/'             # Redirect unauthenticated users here
LOGIN_REDIRECT_URL = '/dashboard/rigs/'    # After login, go to Fleet Overview
LOGOUT_REDIRECT_URL = '/accounts/login/'   # After logout, go to login page
```

**Password reset flow (4 endpoints):**
1. `GET/POST /accounts/password-reset/` — User enters email, system sends reset link
2. `GET /accounts/password-reset/done/` — Confirmation that email was sent
3. `GET/POST /accounts/reset/<uidb64>/<token>/` — User sets new password (token valid 3 days)
4. `GET /accounts/reset/done/` — Success page with link to login

**Email backend:** Console by development (prints to terminal), Gmail SMTP in production (configured via `EMAIL_HOST` env var).

**Cookie settings for local testing:**
```
SESSION_COOKIE_SECURE = False    # HTTP without TLS
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
ALLOWED_HOSTS = '*'              # Accept any host (local testing only)
CSRF_TRUSTED_ORIGINS = ['http://*', 'https://*']
```

### 7.5 Email / SMTP Configuration

The platform uses email for password recovery. Two modes are supported:

**Development (default):** Console backend — emails are printed to stdout, not sent.

```python
# .env — no EMAIL_HOST set → console backend
# Emails appear in Gunicorn logs / terminal output
```

**Production (Gmail SMTP):** Real emails sent via Gmail.

```python
# .env — set EMAIL_HOST to enable SMTP
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=youragent@gmail.com
EMAIL_HOST_PASSWORD=abcd efgh ijkl mnop   # 16-char app-specific password
DEFAULT_FROM_EMAIL=noreply@yourdomain.com
```

**Gmail App Password setup:**
1. Enable 2-Factor Authentication on the Google account
2. Go to https://myaccount.google.com/apppasswords
3. Select app: "Mail", device: "Other (Custom name)" → "GPU Rig Monitor"
4. Copy the 16-character password (ignore spaces)
5. Set as `EMAIL_HOST_PASSWORD` in `.env`

**Sending limits:** Free Gmail accounts can send ~500 emails/day. For a monitoring platform with <100 password resets/day, this is sufficient.

**Troubleshooting:**
- If `EMAIL_HOST` is not set, console backend is used automatically
- Check `gunicorn-error.log` for SMTP authentication errors
- Gmail may block sign-ins from "less secure apps" — use App Passwords, not account password

---

## 8. Operational Runbook

### 8.1 Deployment Procedure

```bash
# Sync code from workspace to /opt
cd $HOME/workspace/GPU-Rig-Monitoring-Platform
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

The platform uses **tiered compaction** to manage long-term storage growth. Without retention, 1,000 rigs would accumulate ~487 GB/month. With compaction: ~23 GB/month (95% savings).

#### Retention Tiers

| Tier | Age | Bucket | Rows/Day/Rig | Savings |
|---|---|---|---|---|
| Raw | 0-1 day | 1-minute | 1,440 | — |
| Compacted | 1-31 days | 1-hour | 24 | 60× |
| Deleted | 31+ days | — | 0 | 100× |

#### Management Commands

Two Django management commands handle retention:

**`compact_data`** — Aggregates old data into larger time buckets:
|- Single phase: data > 1 day → 1-hour buckets
|- Aggregation: AVG (temperature, utilization, power), SUM (network bytes, error_count), LAST (GPU model names, GPU UUIDs, uptime), MAX (uptime_s)
|- Child tables (GPU, storage, network, gpu_process) compacted FIRST; parent table (`metrics_metricsnapshot`) compacted LAST
- FK-safe: parent rows still referenced by children are excluded from compaction via NOT EXISTS subqueries

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
|| `metrics_metricsnapshot` | UPSERT | Per-heartbeat metrics for charts (cpu, memory, uptime, error_count) |
| `metrics_gpumetric` | UPSERT | Per-GPU metrics (1 row per GPU) |
|| `metrics_storagemetric` | UPSERT | Per-disk metrics |
|| `metrics_networkmetric` | UPSERT | Per-interface metrics |
|| `metrics_latest_docker_container` | DELETE+INSERT | Latest container snapshot (image, status, created, status_text) |
|| `metrics_latestsnapshot` | UPSERT | Denormalized display cache (CPU, memory, motherboard, software, GPU/storage/network JSON arrays) |
|| `rig_status_event` | INSERT (conditional) | Only on status transitions |
|| **Total** | **~15-50 writes** | Depending on GPU/disk/container count |

**Measured ingest performance (typical rig, 1 GPU):**
- Total time: **70ms** (38ms DB + 32ms Python overhead)
- DB queries: 41 (7 SELECT, 7 UPDATE, 7 INSERT, 2 DELETE, 18 other)
- Avg query time: 0.9ms

**Measured ingest performance (large rig, 8 GPUs, 5 disks, 3 NICs, 10 containers, 20 processes):**
- Total time: **266ms** (161ms DB + 105ms Python overhead)
- DB queries: 203 (31 SELECT, 29 UPDATE, 57 INSERT, 2 DELETE, 84 other)
- Avg query time: 0.8ms

**Bottleneck breakdown (large payload):**
- NetworkMetric delta calculation: 58ms (22%) — SELECT previous row per interface
- LatestSnapshot JSON serialization: 39ms (15%) — 35+ field defaults with large JSON arrays
- GPUMetric bulk insert: 15ms (6%) — 8 GPUs × 2 queries each
- LatestDockerContainer delete+insert: 8ms (3%) — 10 containers
- Python overhead: 105ms (39%) — DRF serialization, JSON array building, ORM overhead

**Peak throughput:**
- 50 RPS × 30 writes avg = **1,500 writes/sec** at burst
- PostgreSQL on NVMe sustains 2,000-5,000+ writes/sec
- **Utilization: ~30-75% of peak DB capacity** at max burst
- Sustained (1,000 rigs/min): ~250 writes/sec = **~5% of capacity**

**Scaling headroom:** The system can handle ~2,000 rigs at 1-minute intervals before reaching 50% DB write capacity. The NetworkMetric delta calculation is the only significant per-interface bottleneck.

### 10.3 Query Performance Budgets

| Query | Frequency | Optimization | Budget |
|-------|-----------|-------------|--------|
| `IngestView` upsert | Every 60s/rig | `update_or_create` + `unique_together` | < 200 ms |
|| `rig_list` fleet table | Every 30s/browser | Single-row lookup from `LatestSnapshot` (includes GPU data as JSON arrays) | < 50 ms |
|| `htmx_metrics` live cards | Every 30s/browser | Single-row lookup from `LatestSnapshot` (GPU, storage, network) + LatestDockerContainer + GPUProcessMetric | < 100 ms |
|| `ChartDataView` per chart | On demand (↻) | Time-range filter, SQL aggregation (TruncHour + Avg/Sum) | < 200 ms |
| `update_rig_status` | Every 2 min | Bulk `update()`, no per-row queries | < 1 s |

**Concurrency:** 10 dashboard users × 3 pages/min = ~0.5 QPS read load — statistically insignificant vs. agent write load.

### 10.4 Resource Sizing

| Resource | Minimum | Recommended | Justification |
|----------|---------|-------------|---------------|
| vCPU | 4 | 4-8 | Gunicorn: (2 × cores) + 1 workers |
| RAM | 16 GB | 16-32 GB | PG shared_buffers ~4-6 GB + Gunicorn ~1.3 GB + OS ~1.5 GB |
| Storage | 500 GB NVMe | 1 TB+ NVMe | NVMe mandatory for write IOPS. ~1.6 GB/day growth at 100 rigs, ~16 GB/day at 1,000 rigs. With compaction: ~0.7 GB/day at 1,000 rigs. |
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
| Hard timeout | Mock collector `time.sleep(60)` | `signal.alarm(30)` triggers `TimeoutError`, agent exits cleanly |

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
1. Update `IngestSerializer.validate_schema_version()` to accept the new version string
2. Add/update model fields and serializer logic to handle the new payload structure
3. Old agents continue sending their schema version → accepted by `validate_schema_version`
4. New agents send the new schema version → also accepted
5. Deprecation lifecycle: Day 0 (deploy) → Day 30 (recommend upgrade) → Day 180 (deprecated) → Day 365 (dropped)
6. The `DATA_FLOW_ANALYSIS.md` document is updated with new field mappings

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
|| `dashboard/templatetags/gpu_filters.py` | Template filters: gpu_model_name, gpu_model_short, gpu_compact_summary_json, gpu_temp_cell_json, gpu_util_cell_json, gpu_fan_cell_json, time_since, last_seen_short, format_iops, format_throughput_mb, max_disk_util, format_bytes_total |
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
| GET | `/api/v1/rigs/<uuid>/metrics/` | Session | Latest metrics JSON | No |
| GET | `/api/v1/rigs/<uuid>/chart-data/` | Session | Historical chart JSON | No (↻ button) |
| GET | `/dashboard/rigs/` | Session | Fleet Overview (initial load) | No |
| GET | `/dashboard/rigs/` (HX-Request) | Session | Fleet table partial (30s poll) | `#rig-table-container-clock` |
| GET | `/dashboard/rigs/<uuid>/` | Session | Rig detail page | No |
| GET | `/dashboard/rigs/<uuid>/htmx-metrics/` | Session | Live metrics partial (30s poll) | `#metrics-container-clock` |
| GET | `/dashboard/rigs/<uuid>/htmx-status/` | Session | Status badge partial (15s poll) | `#rig-status-container-clock` |
| POST | `/dashboard/rigs/<uuid>/rename/` | Session | Rename rig | No |
| POST | `/dashboard/rigs/<uuid>/delete/` | Session | Delete rig | No |
| POST | `/dashboard/rigs/<uuid>/tags/<tag_id>/toggle/` | Session | Toggle tag | No |
| GET | `/accounts/login/` | None | Login page | No |
| GET | `/accounts/register/` | None | Registration page | No |
| POST | `/accounts/logout/` | Session | Logout | No |
| GET/POST | `/accounts/profile/` | Session | Profile + change password | No |
| GET | `/accounts/api-keys/` | Session | API key list | No |
| POST | `/accounts/api-keys/create/` | Session | Create API key | No |
| POST | `/accounts/api-keys/<key_id>/revoke/` | Session | Revoke API key | No |
|| GET/POST | `/accounts/tags/` | Session | Tag CRUD | No |
|| GET/POST | `/accounts/password-reset/` | None | Request password reset | No |
|| GET | `/accounts/password-reset/done/` | None | Reset email sent | No |
|| GET/POST | `/accounts/reset/<uidb64>/<token>/` | None | Set new password | No |
|| GET | `/accounts/reset/done/` | None | Reset complete | No |

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
