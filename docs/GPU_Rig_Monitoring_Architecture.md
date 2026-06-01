# GPU Rig Monitoring Platform — Architecture Document

**Version:** 1.0  
**Status:** Implementation-Ready Blueprint  
**Classification:** Technical Architecture Document

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [Client Agent Specification](#3-client-agent-specification)
4. [Server Architecture](#4-server-architecture)
5. [Dashboard Specification](#5-dashboard-specification)
6. [Data Model & Schema Versioning](#6-data-model--schema-versioning)
7. [Security Architecture](#7-security-architecture)
8. [Operational Architecture](#8-operational-architecture)
9. [Performance & Scaling Analysis](#9-performance--scaling-analysis)
10. [Testing Strategy](#10-testing-strategy)
11. [Appendices](#11-appendices)

---

## 1. Executive Summary

This document defines the comprehensive, implementation-ready architecture for the **GPU Rig Monitoring Platform v1.0**. It translates detailed business and operational requirements into a deterministic technical blueprint, guiding the development, deployment, and day-two operations of the system.

### 1.1 Purpose & Scope

#### 1.1.1 System Purpose

The platform is a specialized, multi-user telemetry and monitoring dashboard designed explicitly for GPU rigs serving AI/LLM workloads. Unlike generic infrastructure monitors, this system provides deep visibility into the unique failure modes of AI servers, including VRAM fragmentation, PCIe throughput bottlenecks, GPU-attached process mapping, and Docker container instability.

It empowers fleet operators to assess real-time health, analyze historical performance trends, and inspect deep hardware/software states without relying on manual SSH access.

#### 1.1.2 Architectural Scope

The system is bounded by three primary components operating within a strict single-VPS topology:

- **Client Agent:** A lightweight, fault-tolerant Python script executed via cron every 60 seconds on target rigs. It collects extensive hardware, software, and AI-specific metrics, constructs a versioned JSON payload, and delivers it securely over HTTPS.
- **Ingestion & Storage Server:** A Django-based application running on a single Ubuntu VPS. It utilizes Django REST Framework (DRF) for secure API ingestion, PostgreSQL for relational inventory/audit data, and TimescaleDB for high-performance time-series metric storage and aggregation.
- **Hypermedia Dashboard:** A server-rendered web interface built with Django Templates and HTMX. It provides a highly responsive, low-complexity user experience with 30-second polling, eliminating the need for heavy SPA frameworks or WebSocket infrastructure.

#### 1.1.3 Target Scale & Topology

The v1 architecture is explicitly designed for vertical scaling on a single Ubuntu VPS, targeting a ceiling of **~1,000 rigs** reporting at 1-minute intervals. This constraint intentionally avoids the operational overhead of distributed microservices, message brokers, or multi-region clustering.

### 1.2 Non-Goals & Out-of-Scope (v1)

| Category | Excluded Capability | Rationale |
|---|---|---|
| Data Ingestion | Full Log Aggregation (e.g., ELK, Loki) | Storing full journalctl or application logs transforms the system into a heavy log-management platform. v1 strictly limits error tracking to the latest deduplicated critical errors with timestamps. |
| Operations | Active Alerting & Notifications | No PagerDuty, email, or Slack webhook integrations. v1 relies on visual dashboard indicators (Stale/Offline badges, redlining metrics) for human-in-the-loop operations. |
| Topology | Distributed Deployment / Clustering | No Kubernetes, no separate worker nodes, no Celery/RabbitMQ queues. Background tasks are handled by native systemd timers and TimescaleDB continuous aggregates. |
| Extensibility | Public API / Third-Party Webhooks | The REST API is strictly internal, secured by user-scoped API keys for agent ingestion and session cookies for the dashboard. |
| Remediation | Remote Command Execution | The agent is strictly read-only. It cannot execute shell commands, restart services, or modify rig configurations remotely. |

### 1.3 Success Metrics & KPIs

#### 1.3.1 Ingestion & Pipeline Reliability

| Metric | Target | Validation Method |
|---|---|---|
| Payload Acceptance Rate | > 99.9% | Ratio of 200 OK / 202 Accepted vs 4xx/5xx errors under load |
| Idempotency Accuracy | 100% | Zero duplicate rows created in TimescaleDB during simulated network retries |
| Agent Overhead | < 2% CPU, < 50 MB RAM | Measured via psutil on the host rig during the 60-second cron execution window |
| Network Footprint | < 2.0 KB per payload | Average gzip-compressed payload size over a 24-hour period |

#### 1.3.2 Performance & Latency Budgets

| Metric | Target | Validation Method |
|---|---|---|
| Ingest API Latency (p95) | < 200 ms | Measured at the Gunicorn worker level during 1,000-rig Locust load tests |
| Dashboard Chart Load | < 500 ms | Time from HTMX trigger to Chart.js render for a 24-hour historical view |
| Fleet List Polling Swap | < 100 ms | Time for Nginx to serve and browser to morphdom-swap the 50-row fleet table partial |

#### 1.3.3 Operational & Resource Efficiency

| Metric | Target | Validation Method |
|---|---|---|
| VPS CPU Saturation | < 60% average | Measured via netdata/htop on the 4-vCPU VPS at peak 1,000-rig burst (50 RPS) |
| Database Storage Growth | < 5 GB / day | Raw hypertable size + indexes at 1,000 rigs, prior to 14-day compression/drop policies |
| Time-to-First-Payload | < 2 minutes | Time from executing the agent install script to the rig appearing as "Online" on the dashboard |
| Backup Restore Validity | 100% success | Monthly automated CI/CD pipeline that provisions a dummy VPS, restores the latest pg_dump, and passes `Django check --deploy` |

---

## 2. System Architecture Overview

### 2.1 High-Level Component Diagram (C4 Level 2)

The system follows a hub-and-spoke telemetry model with three primary container boundaries: Client Agent, Ingestion/API Server, and Dashboard/UI. All components communicate over HTTPS or internal loopback.

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
                                                                │ HTTP/1.1
                                                                │ (TLS terminated)
┌───────────────────────────────────────────────────────────────┼──────────┐
│                   SINGLE UBUNTU VPS (Trusted)                 │          │
│                                                               ▼          │
│  ┌─────────────────┐    TCP/5432    ┌──────────────────────────────┐    │
│  │ Django + DRF    │ ────────────→  │ PostgreSQL + TimescaleDB     │    │
│  │ Application     │                │                              │    │
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

#### Component Responsibilities

| Component | Technology | Responsibility |
|---|---|---|
| Client Agent | Python 3.10+, psutil, pynvml, requests | Collects metrics, constructs JSON payload, handles retry/idempotency, runs via cron |
| Nginx Edge | Nginx | TLS termination, request routing, rate limiting, static asset serving |
| Django App | Django 5.x, DRF, Gunicorn | Auth, schema validation, payload routing, RBAC, dashboard rendering |
| PostgreSQL + TimescaleDB | PG 16, TimescaleDB 2.14+ | Relational storage, hypertable time-series, continuous aggregates |
| Dashboard UI | Django Templates + HTMX 2.0 | Server-rendered pages, 30s polling, chart data, tag filtering |

### 2.2 Deployment Topology

#### Infrastructure Baseline

| Resource | Specification |
|---|---|
| OS | Ubuntu 22.04 / 24.04 LTS |
| Compute | 4–8 vCPU, 16–32 GB RAM, 500 GB+ NVMe SSD |
| Network | Single public IPv4, DNS A record |
| TLS | Let's Encrypt (certbot), auto-renew via systemd timer |

#### Service & Port Matrix

| Service | Bind Address | Port | Protocol | Access Control |
|---|---|---|---|---|
| Nginx | 0.0.0.0 | 80 | HTTP | Public |
| Nginx | 0.0.0.0 | 443 | HTTPS | Public (TLS enforced) |
| Gunicorn | 127.0.0.1 | 8000 | HTTP | Internal only |
| PostgreSQL | 127.0.0.1 | 5432 | TCP | Internal only, SCRAM-SHA-256 |
| SSH | 0.0.0.0 | 22 | TCP | Restrict to admin IPs |

#### Firewall Rules (ufw)

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

### 2.3 Data Flow Diagrams

#### 2.3.1 Agent Ingestion Sequence

```
Cron (Rig) → Python Agent → Nginx → DRF Ingest View → API Key Middleware → TimescaleDB/PG
    │              │            │          │                    │                    │
    │─ Trigger ──→ │            │          │                    │                    │
    │              │─ Collect → │          │                    │                    │
    │              │─ Build ──→ │          │                    │                    │
    │              │─ POST ───→ │─ Forward→│─ Validate ──────→ │─ Verify ────────→ │
    │              │            │          │   X-API-Key        │   Argon2id         │
    │              │← Response ─│←─────────│←── 200/202/4xx ──│                    │
    │              │─ Log/Exit  │          │                    │                    │
```

#### 2.3.2 Ingestion Contract

| Step | Requirement | Implementation |
|---|---|---|
| Auth | API key on every request | `X-API-Key` header, Argon2id hash comparison |
| Idempotency | Avoid duplicate storage | `UNIQUE(rig_uuid, schema_version, timestamp)` + `ON CONFLICT` |
| Retry Logic | Safe failure recovery | Exponential backoff (1s → 2s → 4s), max 3 attempts |
| Partial Failures | Tolerate metric gaps | Agent wraps collectors in try/except, missing fields omitted |
| Payload Size | Network efficiency | Gzip if > 8 KB, target < 2 KB compressed |

### 2.4 Trust Boundaries & Security Zones

| Zone | Trust Level | Components | Enforcement |
|---|---|---|---|
| Z1: External Fleet | Untrusted | Client Agents, Internet | TLS 1.3, API key auth, rate limiting |
| Z2: Edge/DMZ | Semi-Trusted | Nginx, certbot, UFW | TLS termination, connection limits, size caps |
| Z3: Application | Trusted | Django, Gunicorn, DRF | Session auth, CSRF, RBAC, audit logging |
| Z4: Data Layer | Highly Trusted | PostgreSQL, TimescaleDB | localhost-only, least-privilege roles, encrypted backups |

#### Boundary Validation Matrix

| Boundary | Threat | Mitigation |
|---|---|---|
| Z1 → Z2 | MITM, Replay, Flood | HTTPS, HSTS, rate limiting, TLS 1.3 only |
| Z2 → Z3 | Auth Bypass, Payload Injection | DRF auth, strict JSON schema validation |
| Z3 → Z4 | SQLi, Data Leakage | ORM only, separate DB roles |
| User → Z3 | XSS, CSRF, Session Hijacking | CSRF middleware, Secure cookies, CSP headers |

#### Rate Limiting Strategy

| Layer | Algorithm | Threshold | Action |
|---|---|---|---|
| Nginx | Fixed Window | 10 req/s per IP | 429 |
| DRF | Sliding Window | 2 req/min per key | 429 + backoff hint |
| Global | Connection limit | 500 concurrent | Nginx worker tuning |

---

## 3. Client Agent Specification

### 3.1 Runtime Environment

| Category | Specification |
|---|---|
| Python Version | 3.10+ |
| Installation Path | `/opt/monitoring-agent/` |
| Virtual Env | `/opt/monitoring-agent/venv/` |
| Core Deps | psutil, pynvml, py-cpuinfo, requests, docker, pyyaml |
| System Binaries | smartmontools, nvme-cli, lm-sensors |
| Execution User | `monitoring-agent` (system, no-login) |

### 3.2 Configuration Management

**Path:** `/etc/monitoring-agent/config.yaml` (mode 0600)

```yaml
rig_uuid: "auto"
api_key: ""
server_endpoint: "https://monitor.example.com"
expected_gpu_count: 0
collection_timeout_s: 45
retry_attempts: 3
debug_mode: false
```

**UUID Lifecycle:** Generated via `uuid.uuid4()` on first run, persisted to config, never regenerated. Ownership bound server-side on first ingest.

### 3.3 Metric Collection Modules

Each module is wrapped in an isolated `try/except` block. Failures are logged and omitted from the payload.

| Module | Primary Source | Output Fields | Error Handling |
|---|---|---|---|
| CPU | py-cpuinfo, psutil | model, cores, load, util%, temp | Omit temp if None |
| Memory | psirtual_memory, swap | total, used, free, cached, swap | None (always available) |
| Motherboard | /sys/class/dmi/id/board_* | manufacturer, model, bios | Skip if permission denied |
| Storage | psutil + smartctl/nvme | device, capacity, usage%, smart health | Catch CalledProcessError |
| Network | psutil + /sys/class/net/*/speed | iface, ipv4, speed, rx/tx bytes | Skip loopback |
| GPU | pynvml (NVIDIA ML) | uuid, model, mem, util%, temp, power | Handle NVMLError_NotSupported |
| Docker | docker SDK | containers: [{name, image, status}] | Log warning if failed |
| Software | platform, nvidia-smi, docker version | hostname, kernel, driver, docker | Partial failure tolerated |
| Errors | journalctl -p err..crit | [{source, message, timestamp}] | Truncate to 20 entries |

### 3.4 Payload Construction & Serialization

#### JSON Schema (v1.0) — Key Structure

```json
{
  "rig_uuid": "UUIDv4",
  "schema_version": "1.0",
  "agent_version": "1.0.0",
  "timestamp": "ISO 8601 UTC",
  "inventory": { "cpu": {}, "memory": {}, "gpus": [], ... },
  "metrics": { "cpu": {}, "gpus": [], "docker_containers": [], ... },
  "software": { "hostname": "...", "kernel": "..." },
  "errors": [{ "source": "kernel", "message": "...", "timestamp": "..." }]
}
```

#### Serialization Rules

| Rule | Specification |
|---|---|
| Nullability | Failed metrics are `null`, not omitted |
| Types | Strict typing, no implicit coercion |
| Compression | gzip if > 8 KB uncompressed |
| Size Budget | Target < 20 KB compressed, reject > 2 MB |

### 3.5 Transport Layer

| Parameter | Value |
|---|---|
| Strategy | Exponential backoff with jitter |
| Sequence | 1s → 2s → 4s (max 3 attempts) |
| Jitter | ±20% random delay |
| Hard Timeout | 45s via `signal.alarm(45)` |

#### Server Response Codes

| Code | Meaning | Agent Action |
|---|---|---|
| 200 | New payload accepted | Log OK |
| 202 | Duplicate (idempotent) | Log DUP, ignore |
| 400 | Validation failed | Log ERR, retry next cycle |
| 401 | Invalid API key | Check config |
| 429 | Rate limit hit | Back off |
| 5xx | Server error | Retry per backoff |

### 3.6 Execution Guardrails

```bash
# /etc/cron.d/monitoring-agent
* * * * * monitoring-agent flock -n /var/lock/monitoring-agent.lock /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/run.py >> /var/log/monitoring-agent/cron.log 2>&1
```

- **flock** prevents overlapping runs
- **nice -n 19** for lowest CPU priority
- **ionice -c 3** for idle I/O priority

### 3.7 Local Logging

| Aspect | Specification |
|---|---|
| Path | `/var/log/monitoring-agent/agent.log` |
| Format | Structured JSON |
| Rotation | 10 MB max, 3 backups |
| Redaction | API keys masked in logs |

---

## 4. Server Architecture

### 4.1 Django Project Structure

| App | Purpose | Key Files |
|---|---|---|
| `accounts` | Users, API keys, RBAC | `models.py`, `authentication.py`, `views.py` |
| `rigs` | Rig inventory, tags, status | `models.py`, `update_rig_status` command |
| `metrics_app` | Ingestion, time-series, snapshots | `models.py`, `serializers.py`, `setup_timescale` command |
| `dashboard` | HTMX views, polling endpoints | `views.py`, templates |
| `audit` | Immutable audit logs | `models.py`, `middleware.py` |

### 4.2 Authentication & Authorization

#### Dual-Context Authentication

| Context | Mechanism | Transport |
|---|---|---|
| Agent Ingestion | `APIKeyAuthentication` | `X-API-Key` header |
| Dashboard UI | Django Session Auth | Secure, HttpOnly, SameSite=Lax cookie |

#### RBAC Roles

| Role | Permissions |
|---|---|
| Owner | View/edit own rigs, manage own keys, assign tags |
| Admin | View all rigs, reassign ownership (audit-logged), manage all users |
| Viewer | Read-only dashboard access |

### 4.3 Ingestion Pipeline

```
POST /api/v1/ingest/
  → Nginx (TLS, rate limit, payload size check)
  → DRF APIKeyAuthentication (validate key)
  → DRF Throttle (per-key rate limit)
  → IngestSerializer.validate() (schema version, field mapping)
  → DB UPSERT (UNIQUE constraint + ON CONFLICT DO UPDATE)
  → Response (200/202/4xx/429)
```

**Idempotency:** `UNIQUE(rig_uuid, schema_version, timestamp)` with `ON CONFLICT DO NOTHING`. Insert → 200, Conflict → 202.

### 4.4 Data Storage Layer

#### Relational Schema (PostgreSQL)

| Table | Purpose | Key Constraints |
|---|---|---|
| `accounts_apikey` | Ingestion auth | `UNIQUE(user_id, name)`, prefix index |
| `rigs_rig` | Asset registry | `PRIMARY KEY(uuid)`, FK owner_id |
| `rigs_tag` / `rigs_rig_tags` | Fleet grouping | Composite PK for M2M |
| `audit_auditlog` | Immutable ledger | Partitioned by month |

#### Time-Series Schema (TimescaleDB)

```sql
CREATE TABLE metrics_timeseries (
    time            TIMESTAMPTZ NOT NULL,
    rig_uuid        UUID NOT NULL,
    cpu_util        NUMERIC(5,2),
    gpu_temps       NUMERIC(5,2)[],
    gpu_utils       NUMERIC(5,2)[],
    disk_usage_max  NUMERIC(5,2)
);
SELECT create_hypertable('metrics_timeseries', 'time',
    chunk_time_interval => INTERVAL '1 day');
SELECT add_compression_policy('metrics_timeseries', INTERVAL '7 days');
```

#### Retention Strategy

| Layer | Storage | Retention |
|---|---|---|
| Raw metrics | `metrics_timeseries` | 7–14 days |
| Hourly aggregates | `metrics_hourly_agg` | 90 days |
| Daily aggregates | `metrics_daily_agg` | 1 year |

### 4.5 API Layer

#### Endpoint Catalog

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/ingest/` | Telemetry submission | API Key |
| GET | `/api/v1/health/` | Health check | None |
| GET | `/api/v1/rigs/` | List rigs | Session |
| GET | `/api/v1/rigs/<uuid>/htmx-metrics/` | Dashboard polling | Session |
| GET | `/api/v1/rigs/<uuid>/chart-data/` | Chart JSON | Session |

### 4.6 Background Tasks

| Task | Mechanism | Frequency |
|---|---|---|
| Rig status update | `update_rig_status` command | Every 2 minutes |
| Chunk drop | Timescale policy | Auto (7 days) |
| Aggregate refresh | Timescale policy | Auto (1 hour) |
| Error pruning | Django command | Daily |
| DB backup | `backup_db.sh` + rclone | Daily |

### 4.7 Schema Versioning Protocol

| Change Type | Version Bump |
|---|---|
| Add optional field | None (additive) |
| Rename field | Major (2.0) |
| Change data type | Major (2.0) |

**Server-Side Routing:**
```python
SERIALIZER_MAP = {"1.0": IngestSerializerV1, "1.1": IngestSerializerV1_1}
```

**Agent Deprecation Lifecycle:** Day 0 (deploy) → Day 30 (upgrade recommended) → Day 180 (deprecated, shim) → Day 365 (dropped, 400 response).

### 4.8 Audit Logging & Observability

**Audit Events:** `apikey.created`, `apikey.revoked`, `rig.enrolled`, `rig.reassigned`, `tag.created/deleted`, `user.login_failed`

**Health Endpoint Response:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "db_connection": "ok",
  "timescale_version": "2.14.1",
  "active_rigs": 842,
  "ingest_qps": 14.2
}
```

---

## 5. Dashboard Specification

### 5.1 UI Architecture

The dashboard follows a **Hypermedia-Driven Application (HDA)** pattern. HTMX swaps HTML fragments; the server is the single source of truth for UI state.

#### HTMX Patterns Used

| Pattern | Attributes | Use Case |
|---|---|---|
| Polling | `hx-trigger="every 30s"` | Fleet table, rig detail metrics |
| Lazy Loading | `hx-trigger="revealed"` | Historical charts |
| Form Submission | `hx-post`, `hx-target` | API key creation, tag assignment |
| OOB Swap | `hx-swap-oob="true"` | Toast notifications |
| Morphdom | `hx-ext="morphdom-swap"` | Preserves scroll position during table swaps |

#### JavaScript Constraints

JS is strictly limited to: Chart.js initialization after HTMX swaps, Tom Select for tag dropdowns, and `htmx:afterSwap` event bridging. No client-side routing or state stores.

### 5.2 Auth & User Management

- **Login/Logout:** Session-based cookie auth, `LoginRequiredMixin` on all `/dashboard/` routes
- **API Key UI:** Generate → show plaintext key once → list with revoke button

### 5.3 Rig List Page (Fleet Overview)

#### Columns

| Column | Source | Rendering |
|---|---|---|
| Rig Name | `rigs_rig.name` | Clickable link |
| Status | `rigs_rig.status` | 🟢 Online / 🟡 Stale / 🔴 Offline |
| Last Seen | `rigs_rig.last_seen` | Relative time |
| GPU Util | Latest snapshot | Progress bar |
| CPU Temp | Latest snapshot | Color-coded text (>85°C red) |
| Tags | `rigs_rigtag` | Colored pills |

**HTMX Polling:** 30s auto-refresh with `morphdom-swap` extension for DOM diffing. Server-side pagination (50 rows per page).

### 5.4 Rig Detail Page

Three tabs:

1. **Static Inventory** — Hardware grid (CPU, GPU, memory, storage, network), software grid (OS, driver, Docker)
2. **Live Status & Errors** — 30s HTMX poll, vitals, Docker containers, deduplicated errors
3. **Historical Charts** — Lazy-loaded, Chart.js, time range selector (1H/6H/24H/7D/30D), queries continuous aggregates for ranges > 24H

### 5.5 Tagging & Filtering

- **Model:** `RigTag` (name, color, owner) + M2M through table
- **UI:** Tom Select multi-select dropdown, color-coded pills on fleet table
- **Filtering:** URL query params → Django ORM `Q` objects

### 5.6 Performance Budgets

| Metric | Target | Strategy |
|---|---|---|
| Initial Page Load | < 800 ms | `select_related`, `prefetch_related`, no N+1 |
| 30s Polling Payload | < 50 KB | HTML fragments only, minified |
| Chart Data Fetch | < 500 ms | Continuous aggregates, < 2000 points |
| Template Rendering | < 100 ms | `{% cache %}` for static elements |

---

## 6. Data Model & Schema Versioning

### 6.1 Five-Layer Data Architecture

| Layer | Storage | Purpose |
|---|---|---|
| Layer 1 | PostgreSQL relational | Users, API keys, rigs, tags, audit events |
| Layer 2 | PostgreSQL denormalized | `metrics_latest_snapshot` — current state per rig |
| Layer 3 | TimescaleDB hypertable | `metrics_timeseries` — historical time-series |
| Layer 4 | PostgreSQL | `metrics_latest_errors` — deduplicated errors |
| Layer 5 | PostgreSQL partitioned | `audit_event` — immutable activity log |

### 6.2 Key Table DDLs

#### `accounts_apikey`
```sql
CREATE TABLE accounts_apikey (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES accounts_user(id) ON DELETE CASCADE,
    name        VARCHAR(64) NOT NULL,
    prefix      VARCHAR(8) NOT NULL UNIQUE,
    key_hash    VARCHAR(255) NOT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ NULL,
    CONSTRAINT unique_name_per_user UNIQUE (user_id, name)
);
CREATE INDEX idx_apikey_prefix ON accounts_apikey(prefix);
```

#### `rigs_rig`
```sql
CREATE TABLE rigs_rig (
    uuid                UUID PRIMARY KEY,
    owner_id            UUID NOT NULL REFERENCES accounts_user(id) ON DELETE RESTRICT,
    name                VARCHAR(128) NOT NULL DEFAULT 'Unnamed Rig',
    expected_gpu_count  INTEGER DEFAULT 0,
    status              VARCHAR(16) DEFAULT 'offline',
    last_seen           TIMESTAMPTZ NULL,
    last_agent_version  VARCHAR(32) NULL,
    enrolled_at         TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT chk_status CHECK (status IN ('online', 'stale', 'offline'))
);
CREATE INDEX idx_rig_owner ON rigs_rig(owner_id);
CREATE INDEX idx_rig_status ON rigs_rig(status);
```

#### `metrics_latest_snapshot` (Denormalized Current State)
```sql
CREATE TABLE metrics_latest_snapshot (
    rig_uuid            UUID PRIMARY KEY REFERENCES rigs_rig(uuid) ON DELETE CASCADE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cpu_util            NUMERIC(5,2),
    cpu_temp            NUMERIC(5,2),
    mem_used_bytes      BIGINT,
    mem_total_bytes     BIGINT,
    gpu_util_max        NUMERIC(5,2),
    gpu_temp_max        NUMERIC(5,2),
    inventory_json      JSONB NOT NULL,
    software_json       JSONB NOT NULL,
    docker_json         JSONB NOT NULL,
    ai_processes_json   JSONB NOT NULL
);
```

#### `metrics_timeseries` (TimescaleDB Hypertable)
```sql
CREATE TABLE metrics_timeseries (
    time                TIMESTAMPTZ NOT NULL,
    rig_uuid            UUID NOT NULL REFERENCES rigs_rig(uuid) ON DELETE CASCADE,
    cpu_util            NUMERIC(5,2),
    cpu_temp            NUMERIC(5,2),
    mem_used_bytes      BIGINT,
    gpu_utils           NUMERIC(5,2)[],
    gpu_temps           NUMERIC(5,2)[],
    gpu_power_draws     NUMERIC(5,2)[],
    disk_usage_max      NUMERIC(5,2)
);
SELECT create_hypertable('metrics_timeseries', 'time',
    chunk_time_interval => INTERVAL '1 day');
CREATE INDEX idx_timeseries_rig_time ON metrics_timeseries (rig_uuid, time DESC);
```

#### `metrics_latest_errors`
```sql
CREATE TABLE metrics_latest_errors (
    id              BIGSERIAL PRIMARY KEY,
    rig_uuid        UUID NOT NULL,
    source          VARCHAR(32) NOT NULL,
    message         TEXT NOT NULL,
    first_seen      TIMESTAMPTZ NOT NULL,
    last_seen       TIMESTAMPTZ NOT NULL,
    occurrence_count INTEGER DEFAULT 1,
    CONSTRAINT unique_rig_source UNIQUE (rig_uuid, source, message)
);
```

#### `audit_event`
```sql
CREATE TABLE audit_event (
    id          BIGSERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_id    UUID REFERENCES accounts_user(id) ON DELETE SET NULL,
    action      VARCHAR(64) NOT NULL,
    target_type VARCHAR(32) NOT NULL,
    target_id   UUID NOT NULL,
    ip_address  INET,
    metadata    JSONB
) PARTITION BY RANGE (timestamp);
```

### 6.3 Continuous Aggregates

**Hourly (retained 90 days):**
```sql
CREATE MATERIALIZED VIEW metrics_hourly_agg
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    rig_uuid,
    AVG(cpu_util) AS avg_cpu_util,
    MAX(cpu_temp) AS max_cpu_temp,
    AVG(gpu_utils[1]) AS avg_gpu_util_0
FROM metrics_timeseries
GROUP BY bucket, rig_uuid;
SELECT add_continuous_aggregate_policy('metrics_hourly_agg',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');
SELECT add_retention_policy('metrics_hourly_agg', INTERVAL '90 days');
```

**Daily (retained 1 year):** Similar structure with 1-day bucket.

**Raw data retention:** 14 days via `add_retention_policy`.

### 6.4 Schema Versioning Rules

| Change Type | Version Bump |
|---|---|
| Add optional field | None |
| Rename field | Major (2.0) |
| Change data type | Major (2.0) |

**Server-side routing:** `SERIALIZER_MAP = {"1.0": V1Serializer, "1.1": V1_1Serializer}`

---

## 7. Security Architecture

### 7.1 STRIDE Threat Model

| Threat | Attack Vector | Mitigation | Enforcement Layer |
|---|---|---|---|
| Spoofing | Fake metrics for victim's rig_uuid | UUID permanently bound to first API key owner | DRF `IsRigOwner` |
| Tampering | MITM alters payload | TLS 1.3, HSTS | Nginx / Let's Encrypt |
| Repudiation | Admin denies rig reassignment | Immutable audit ledger | DB triggers |
| Information Disclosure | Backup theft → plaintext keys | Argon2id hashing | Django `make_password()` |
| DoS | Flood /api/v1/ingest/ | Nginx + DRF throttling | Multi-layer |
| Privilege Escalation | IDOR to view other users' rigs | QuerySet filtering by `request.user` | Django ORM |

### 7.2 API Key Lifecycle

- **Generation:** `secrets.token_urlsafe(48)`, prefix stored in plaintext, full key hashed with Argon2id
- **Validation:** Constant-time `check_password()`, timing attack mitigation with dummy hash on miss
- **Revocation:** `is_active = False` takes effect immediately (no caching)

### 7.3 Transport Security

| Setting | Value |
|---|---|
| Protocol | TLS 1.3 only |
| HSTS | `max-age=63072000; includeSubDomains; preload` |
| Certificate | Let's Encrypt, auto-renew via systemd timer |
| Headers | `X-Frame-Options: DENY`, CSP, `X-Content-Type-Options: nosniff` |

### 7.4 Rate Limiting

| Layer | Algorithm | Threshold |
|---|---|---|
| Nginx | Fixed Window | 10 req/s per IP |
| DRF | Scoped Sliding Window | 2/min per API key |

### 7.5 Secret Management

- All secrets via environment variables (`.env` file, mode 600)
- No hardcoded secrets in `settings.py`
- Django uses `PASSWORD_HASHERS` with `Argon2PasswordHasher` first
- DB role with least privilege, `pg_hba.conf` restricted to `127.0.0.1/32`

---

## 8. Operational Architecture

### 8.1 Directory Structure

```
/opt/gpu_monitor/          # Django project root (owned by monitoring:monitoring)
├── venv/                  # Python virtual environment
├── gpu_monitor/           # Django settings, urls, wsgi
├── accounts/              # Users, API keys, auth
├── rigs/                  # Rig inventory
├── metrics_app/           # Ingestion, time-series
├── dashboard/             # HTMX views
├── audit/                 # Audit logging
├── templates/             # HTML templates
├── deploy/                # Nginx config, systemd units, install scripts
├── logs/                  # Application logs
└── .env                   # Environment variables (0600)
```

### 8.2 Gunicorn Systemd Unit

```ini
[Unit]
Description=GPU Rig Monitor - Gunicorn
After=network.target postgresql.service

[Service]
Type=notify
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/gunicorn \
    gpu_monitor.wsgi:application \
    --bind 127.0.0.1:8000 \
    --workers 4 --timeout 30
ExecReload=/bin/kill -s HUP $MAINPID
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### 8.3 Backup Strategy

**Daily pg_dump + rclone to offsite storage:**

```bash
#!/bin/bash
pg_dump -U postgres -Fc gpu_monitor > /var/backups/postgres/gpu_monitor_$(date +%Y%m%d).dump
gzip /var/backups/postgres/gpu_monitor_*.dump
rclone copy /var/backups/postgres/ b2:gpu-monitor-backups/db/
find /var/backups/postgres -name "*.dump.gz" -mtime +7 -delete
```

**Monthly automated restore test:** Spins up temporary VPS, restores backup, validates with `python manage.py check --deploy`.

### 8.4 Meta-Monitoring

| Probe | Tool | Frequency | Alert Threshold |
|---|---|---|---|
| HTTPS & TLS Cert | UptimeRobot | 60s | HTTP != 200, cert < 14 days |
| Health Endpoint | UptimeRobot JSON parser | 60s | `status != "healthy"` |
| Host Resources | Netdata / HetrixTools | 60s | CPU > 90%, Disk > 85% |

### 8.5 Logging Strategy

- **Format:** Structured JSON (python-json-logger)
- **Correlation IDs:** UUID per request, propagated to all logs
- **Rotation:** `logrotate` daily, compress, retain 14 days

### 8.6 Upgrade Procedures

**Server (Zero-Downtime):**
```bash
cd /opt/gpu_monitor
git pull origin main
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py collectstatic --noinput
systemctl reload gunicorn
```

**Migration Safety Rules:**
1. No `RenameField` or `DeleteModel` in a single deploy
2. Additive-only evolution: add new field → dual-write → backfill → read from new → drop old
3. TimescaleDB: never change column types, add new columns instead

---

## 9. Performance & Scaling Analysis

### 9.1 Payload Size Budget

| Section | Uncompressed | Compressed (gzip) |
|---|---|---|
| Headers & Envelope | ~200 B | ~150 B |
| Inventory | ~2.5 KB | ~600 B |
| Metrics | ~1.5 KB | ~400 B |
| Software & Docker | ~1.5 KB | ~450 B |
| Errors | ~1.0 KB | ~300 B |
| **Total** | **~6.7 KB** | **~1.9 KB** |

### 9.2 Network & Write Throughput

| Metric | Value |
|---|---|
| Average RPS | 1,000 rigs / 60s = **16.67 RPS** |
| Peak RPS (3x burst) | **~50 RPS** |
| Peak Bandwidth | 50 × 1.9 KB = **95 KB/s (0.76 Mbps)** |
| Peak DB Writes | 50 × 8 = **400 writes/sec** |
| PostgreSQL Capacity (NVMe) | **2,000–5,000+ writes/sec** |
| Safety Margin | **~10–20%** of peak |

### 9.3 Resource Sizing

| Resource | Specification | Justification |
|---|---|---|
| CPU | 4 vCPUs | Gunicorn: (2×4)+1 = 9 workers. Handles 50 RPS + background tasks. |
| RAM | 16 GB | PG/TimescaleDB: 4–6 GB. Gunicorn: ~1.3 GB. OS/Nginx: ~1.5 GB. Headroom: ~7 GB. |
| Storage | 250 GB NVMe | Data: ~5 GB/day. 14-day retention: ~70 GB. Backups/Logs: ~50 GB. **NVMe mandatory.** |
| Network | 1 Gbps | Peak < 1 Mbps. |

### 9.4 Horizontal Scaling Path (Future)

| Phase | Fleet Size | Change |
|---|---|---|
| 1,000–2,500 | Read Replica | Route dashboard queries to replica |
| 2,500–5,000 | Ingestion Queue | Redis Streams / RabbitMQ + Celery workers |
| 5,000+ | Edge Proxies + Sharding | Go/Rust edge proxies, TimescaleDB sharding |

---

## 10. Testing Strategy

### 10.1 Unit Tests

#### Agent (pytest + mock)

| Scenario | Assertion |
|---|---|
| Happy path | Payload matches JSON Schema v1.0 |
| GPU driver missing | `gpus: []`, payload still sent |
| SMART failure | `smart_health: null`, agent doesn't abort |
| Network timeout | Exponential backoff verified |
| Hard timeout | `signal.alarm(45)` triggers `TimeoutError` |

#### Server (pytest-django)

| Scenario | Assertion |
|---|---|
| Serializer validation | Rejects missing `rig_uuid`, accepts unknown extra fields |
| Ownership enforcement | User B's rig → 404 for User A |
| Offline detection | `last_seen > 10 min` → `status = "offline"` |
| API key hashing | Argon2id hash stored, plaintext never in DB |

### 10.2 Integration Tests

**Idempotency Proof:**
```python
def test_duplicate_payload(api_client, payload):
    r1 = api_client.post('/api/v1/ingest/', payload)
    assert r1.status_code == 200
    assert MetricSnapshot.objects.count() == 1
    
    r2 = api_client.post('/api/v1/ingest/', payload)
    assert r2.status_code == 202  # Duplicate
    assert MetricSnapshot.objects.count() == 1  # Unchanged
```

**TimescaleDB:** Migration tests, chunk exclusion via `EXPLAIN ANALYZE`, error deduplication (5 identical errors → 1 row, `count=5`).

### 10.3 E2E Tests (Playwright)

1. Log in, generate API key → key visible once in DOM
2. Agent thread sends 3 payloads over 3 minutes
3. Navigate to /dashboard/ → rig appears with 🟢 Online
4. Click rig, switch to charts tab → Chart.js rendered with data
5. Kill agent, wait for stale threshold → badge updates to 🟡 Stale

### 10.4 Load Testing (Locust)

```python
class RigAgent(HttpUser):
    wait_time = between(55, 65)  # Cron jitter around 60s
    
    @task
    def send_telemetry(self):
        self.client.post("/api/v1/ingest/", data=payload,
                        headers={"X-API-Key": self.api_key})
```

**Pass Criteria:** p95 latency < 200 ms, error rate < 0.1%, VPS CPU < 85%.

### 10.5 Contract Testing

Golden payload repository (`tests/contracts/payloads/`): `v1.0_minimal.json`, `v1.0_full.json`, `v1.1_with_ai_metrics.json`. Every PR must pass all historical schema versions.

---

## 11. Appendices

### Appendix A: Glossary & Acronyms

| Term | Definition |
|---|---|
| HDA | Hypermedia-Driven Application. UI pattern where server-rendered HTML fragments drive state changes via HTMX. |
| Hypertable | TimescaleDB abstraction that automatically partitions time-series data into chunks based on time intervals. |
| Continuous Aggregate | TimescaleDB materialized view that incrementally computes aggregations as new data arrives. |
| Idempotency | Property where repeating the same request yields the same system state without side effects. |
| RBAC | Role-Based Access Control. Restricts dashboard/data access to Owner, Admin, or Viewer roles. |
| STRIDE | Threat modeling framework: Spoofing, Tampering, Repudiation, Information Disclosure, DoS, Elevation of Privilege. |
| Schema Versioning | Protocol where `schema_version` in payloads allows the server to route validation logic. |
| Morphdom | HTMX extension that performs DOM diffing during swaps, preserving scroll position and input focus. |
| UFC | Ubuntu Firewall. |
| DRF | Django REST Framework. |
| pynvml | Python bindings for NVIDIA Management Library (NVML). |
| cron | Time-based job scheduler on Unix-like systems. |
| systemd | System and service manager for Linux. |

---

*End of document.*
