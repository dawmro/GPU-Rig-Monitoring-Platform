# GPU Rig Monitoring Platform — Celery Migration Plan

**Version:** 1.0
**Date:** 16 July 2026
**Author:** Senior Django Backend Architect
**Status:** Planning Document — No Code Changes Made

---

## Executive Summary

This document provides a comprehensive plan to migrate the GPU Rig Monitoring Platform's background processing from **cron-based scheduling** and **synchronous Django views** to a **Celery + Redis** architecture. The migration is designed to be **incremental, non-breaking, and production-safe**, allowing the repository owner to implement it manually in well-defined stages.

**Current State:** All background work runs via cron jobs (system-level) and synchronous request/response cycles.
**Target State:** Celery workers + Celery Beat scheduler with Redis broker, organized into dedicated queues.

---

## 1. Current Architecture Analysis

### 1.1 Systemd Services

| Service | Purpose | Process | Restart Policy |
|---------|---------|---------|----------------|
| `gunicorn.service` | Django WSGI application server | 4–8 workers (dynamic based on CPU) | `on-failure`, 5s delay |
| `postgresql.service` | Database server | System-level | Standard |
| `nginx.service` | Reverse proxy, TLS termination, static files | Master + workers | Standard |

**Key observation:** Only Gunicorn runs the Django application. All background tasks execute **outside** the Gunicorn process tree via cron.

---

### 1.2 Cron Jobs (Server-Side)

| Cron File | Schedule | Command | Purpose |
|-----------|----------|---------|---------|
| `/etc/cron.d/rig-status` | `*/2 * * * *` (every 2 min) | `update_rig_status.sh` → `python manage.py update_rig_status` | Marks rigs **Stale** (2–10 min) or **Offline** (>10 min) based on `last_seen` |
| `/etc/cron.d/monitoring-data-cleanup` | `0 3 * * *` (daily 3 AM) | `data_retention.sh` → `compact_data` + `cleanup_old_data` + `VACUUM ANALYZE` | 3-tier compaction + 31-day retention + vacuum |
| `/etc/cron.d/gpu-monitor-backup` | `0 3 * * *` (daily 3 AM) | `backup_db.sh` → `pg_dump` + gzip | PostgreSQL logical backup (7-day retention) |
| `/etc/logrotate.d/gpu-monitor` | Daily (via logrotate) | Rotates gunicorn, cleanup, rig_status logs | Log management |

**Interaction:** All cron jobs are independent. `data_retention.sh` sources `.env` for DB credentials, then calls management commands. The rig status cron runs every 2 minutes and must complete within ~30s to avoid overlap.

---

### 1.3 Cron Jobs (Agent-Side, Per Rig)

| Cron File | Schedule | Command | Purpose |
|-----------|----------|---------|---------|
| `/etc/cron.d/monitoring-agent` | `* * * * *` (every 60s) | `flock` + `run.py` | Collect metrics, POST to `/api/v1/ingest/` |
| `/etc/cron.d/monitoring-agent-update` | Daily at random HH:MM | `check_update.py` | Check for agent updates, self-update |

**Agent flow:** `run.py` collects hardware metrics (CPU, GPU, storage, network, Docker, processes) → builds JSON payload → HTTPS POST to server ingest endpoint. Runs as `monitoring-agent` user with `flock` to prevent overlap.

---

### 1.4 Django Management Commands

| Command | App | Purpose | Called By |
|---------|-----|---------|-----------|
| `update_rig_status` | `rigs` | Mark rigs stale/offline based on `last_seen` | rig-status cron (2 min) |
| `compact_data` | `metrics_app` | 3-tier aggregation: 1m→15m (1–7d), 15m→1h (7–31d) | data_retention.sh (daily) |
| `cleanup_old_data` | `metrics_app` | Delete data >31 days in FK-safe batches | data_retention.sh (daily) |
| `daily_maintenance` | `metrics_app` | Orchestrates: compact → cleanup → VACUUM ANALYZE | Alternative to data_retention.sh |
| `cleanup_audit_log` | `audit` | Delete audit entries >90 days | data_retention.sh (daily) |
| `backfill_historical_data` | `metrics_app` | Generate test data (dev only) | Manual |

**Ingest pipeline (synchronous):** `IngestView.post()` → `process_ingest()` → single `transaction.atomic()` block creates/updates:
- `MetricSnapshot` (parent)
- `GPUMetric` (per GPU)
- `GPUProcessMetric` (per process, delete+recreate)
- `StorageMetric` (per disk, with delta calc from `LatestSnapshot`)
- `NetworkMetric` (per interface, with delta calc)
- `PowerReading` (throttled to 1/min)
- `LatestDockerContainer` (delete+recreate)
- `LatestSnapshot` (denormalized cache for live metrics)

**Latency:** Typical ingest takes **200–800ms** per payload. At 1000 rigs/minute, this is ~16 req/s sustained.

---

### 1.5 Background Processing Summary

| Category | Current Mechanism | Frequency | SLA |
|----------|------------------|-----------|-----|
| Rig status updates | Cron (system) | 2 min | <30s |
| Data compaction | Cron (daily) | 3 AM | Minutes |
| Data deletion | Cron (daily) | 3 AM | Minutes |
| VACUUM ANALYZE | Cron (daily) | 3 AM | Seconds |
| Audit log cleanup | Cron (daily) | 3 AM | Seconds |
| DB backup | Cron (daily) | 3 AM | Minutes |
| Agent metric collection | Cron (per rig) | 60s | <45s |
| Agent self-update | Cron (per rig) | Daily | N/A |
| Telemetry ingest | **Synchronous HTTP** | Per request | <1s |

**Critical path:** The ingest endpoint is **synchronous** and runs in the Gunicorn worker. High ingestion volume directly competes with dashboard requests for worker slots.

---

## 2. Celery Migration Recommendations

### 2.1 Decision Matrix: What Stays, What Moves

| Component | Recommendation | Rationale |
|-----------|----------------|-----------|
| **Gunicorn (Django app server)** | **Keep as systemd service** | Core request handling; Celery workers are separate processes |
| **PostgreSQL** | **Keep as systemd service** | Database is infrastructure, not a task |
| **Nginx** | **Keep as systemd service** | Reverse proxy, TLS, static files |
| **Rig status update (`update_rig_status`)** | **Celery Beat → Celery task (queue: `maintenance`)** | Runs every 2 min, fast (<1s), idempotent, perfect for periodic task |
| **Data compaction (`compact_data`)** | **Celery Beat → Celery task (queue: `maintenance`)** | Long-running (minutes), I/O heavy, should not block web workers |
| **Data cleanup (`cleanup_old_data`)** | **Celery Beat → Celery task (queue: `maintenance`)** | Long-running, batch DELETEs, FK-safe ordering |
| **VACUUM ANALYZE** | **Keep as cron** or **Celery task (queue: `maintenance`)** | Cannot run in transaction; psql call. Can wrap in task or keep cron. **Recommendation: keep as cron** (runs outside Django, no Celery worker needed) |
| **Audit log cleanup** | **Celery Beat → Celery task (queue: `maintenance`)** | Fast, simple DELETE |
| **DB backup (`pg_dump`)** | **Keep as cron** | Runs `pg_dump` binary; no Django ORM needed. Cron is simpler and more reliable for this. |
| **Log rotation** | **Keep as logrotate** | Standard Linux tool, no Celery involvement |
| **Agent metric collection** | **Keep as cron on rigs** | Runs on remote machines; Celery would require agent rewrite. Out of scope. |
| **Agent self-update** | **Keep as cron on rigs** | Same as above |
| **Telemetry ingest (`IngestView`)** | **Async Celery task (queue: `ingest`)** | **HIGH IMPACT**: Move heavy payload processing off Gunicorn workers. Return 202 Accepted immediately. |
| **Chart data queries** | **Keep synchronous (with caching)** | Read-only, already cached 55s. Celery adds latency. |
| **Report generation** | **New Celery task (queue: `reports`)** | Future: PDF/CSV exports, scheduled reports |
| **Notifications/alerts** | **New Celery task (queue: `alerts`)** | Future: email, webhook, PagerDuty on rig down |

---

### 2.2 Recommended Queue Architecture

```
Redis Broker (DB 0)
├── ingest (priority 9)     → 1 worker × 2 concurrency (prefork)
├── maintenance (priority 1-5) → 1 worker × 1 concurrency (prefork)  
└── default/alerts/reports  → 1 worker × 1 concurrency (prefork)
```

**Total: 4 worker processes + 1 beat scheduler = 5 processes** (fits 2 cores with headroom)

| Queue | Purpose | Worker Count | Concurrency | Pool | Timeouts |
|-------|---------|--------------|-------------|------|----------|
| `ingest` | Telemetry payload processing | **1** | **2** | prefork | 300s/240s |
| `maintenance` | Data compaction, cleanup, vacuum | **1** | **1** | prefork | 7200s/6600s |
| `default` | Dashboard, alerts, reports | **1** | **1** | prefork | 300s/240s |

**Beat Scheduler:** Single instance (`celery-beat.service`) — uses `django-celery-beat` DatabaseScheduler
```ini
[Unit]
Description=Celery Ingest Worker %i
After=network.target redis.service postgresql.service
Wants=redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor worker \
    --loglevel=INFO \
    --queues=ingest \
    --concurrency=2 \
    --pool=prefork \
    --hostname=ingest-worker-%i@%h \
    --max-tasks-per-child=100 \
    --time-limit=300 \
    --soft-time-limit=240
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

#### `/etc/systemd/system/celery-maintenance@.service`
```ini
[Unit]
Description=Celery Maintenance Worker %i
After=network.target redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor worker \
    --loglevel=INFO \
    --queues=maintenance \
    --concurrency=1 \
    --pool=prefork \
    --hostname=maint-worker-%i@%h \
    --max-tasks-per-child=10 \
    --time-limit=7200 \
    --soft-time-limit=6600
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### `/etc/systemd/system/celery-default@.service`
```ini
[Unit]
Description=Celery Default Worker %i
After=network.target redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor worker \
    --loglevel=INFO \
    --queues=default,alerts,reports \
    --concurrency=1 \
    --pool=prefork \
    --hostname=default-worker-%i@%h \
    --max-tasks-per-child=50 \
    --time-limit=300 \
    --soft-time-limit=240
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### `/etc/systemd/system/celery-beat.service`
```ini
[Unit]
Description=Celery Beat Scheduler
After=network.target redis.service postgresql.service
Wants=redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor beat \
    --loglevel=INFO \
    --scheduler=django_celery_beat.schedulers:DatabaseScheduler \
    --pidfile=/var/run/celery/beat.pid \
    --schedule=/var/lib/celery/beat-schedule
RuntimeDirectory=celery
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Enable and start:**
```bash
# Ingest workers (scale based on rig count: 1 worker per ~250 rigs)
for i in 1 2 3 4; do sudo systemctl enable --now celery-ingest@$i; done

# Maintenance workers (1-2)
sudo systemctl enable --now celery-maintenance@1
sudo systemctl enable --now celery-maintenance@2

# Default workers (2)
for i in 1 2; do sudo systemctl enable --now celery-default@$i; done

# Beat scheduler (single instance)
sudo systemctl enable --now celery-beat
```

---

### 3.3 Django-Celery Integration

**Files to create:**

1. `gpu_monitor/celery.py` — Celery app configuration
2. `gpu_monitor/__init__.py` — Ensure app loads on Django startup
3. `rigs/tasks.py` — `update_rig_status` task
4. `metrics_app/tasks.py` — `compact_data`, `cleanup_old_data`, `vacuum_analyze`, `process_ingest_payload`
5. `audit/tasks.py` — `cleanup_audit_log` task


---

### Phase 0: Prerequisites (Infrastructure Only — Can Be Done Anytime)

This phase adds **only infrastructure**. No code changes, no cron modifications, no Django config changes.

**Detailed implementation:** See [`CELERY_PHASE0_IMPLEMENTATION.md`](CELERY_PHASE0_IMPLEMENTATION.md)

| Step | Action | Verification |
|------|--------|--------------|
| 1 | Install & configure Redis (localhost, password, maxmemory 2GB, LRU, no RDB) | `redis-cli -a PASS ping` → `PONG` |
| 2 | Install Celery stack in venv (`celery`, `redis`, `django-celery-beat`, `django-celery-results`) | Python imports succeed |
| 3 | Add Redis component vars to `.env` (`REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB_BROKER=0`, `REDIS_DB_RESULTS=1`) | `.env` loads without error |
| 4 | Add Celery settings to `settings.py` (URL builder, `CELERY_*` config, `CELERY_BEAT_SCHEDULER`) | Settings load without error |
| 5 | Add `django_celery_beat` and `django_celery_results` to `INSTALLED_APPS` | Django starts successfully |
| 6 | Run migrations for Celery apps | Tables `django_celery_beat_*` and `django_celery_results_*` created |
| 7 | Test Redis connectivity from Django | Python ping returns `True` |

**What changed / What didn't:**

| ✅ Changed | ❌ Didn't Change |
|-----------|------------------|
| Redis installed & secured | Zero Django code modified |
| Celery packages in venv | Zero cron jobs modified |
| `.env` has Redis component vars | Zero systemd services for Celery yet |
| Settings updated with Celery config | Zero tasks created |
| Migrations run | Zero workers started |
| Connectivity verified | Existing server fully functional |

---


### Phase 1: Infrastructure Only (No Task Migration)
- [ ] Create `gpu_monitor/celery.py` with app config
- [ ] Update `gpu_monitor/__init__.py` to import Celery app
- [ ] Create systemd unit files for workers and beat (as above)
- [ ] Start Redis, verify connectivity
- [ ] Start **one** `celery-default@1` worker and `celery-beat`
- [ ] Verify beat creates schedule entries in DB (`django_celery_beat_periodictask`)
- [ ] **No cron jobs removed yet.** Everything still runs via cron.

**Validation:** `celery -A gpu_monitor inspect ping` returns pong from workers.

---

### Phase 2: Migrate Rig Status Update (Low Risk, High Value)
- [ ] Create `rigs/tasks.py` with `update_rig_status` task (copy logic from management command)
- [ ] Add periodic task in Beat schedule (every 2 min, queue `maintenance`)
- [ ] Deploy, start `celery-maintenance@1` worker
- [ ] **Monitor for 24–48 hours:** Compare rig status transitions with cron logs
- [ ] **Once verified:** Disable `/etc/cron.d/rig-status` (comment out line)
- [ ] Keep management command for manual invocation

**Rollback:** Uncomment cron line, stop maintenance worker.

---

### Phase 3: Migrate Data Maintenance (Medium Risk)
- [ ] Create `metrics_app/tasks.py` with tasks wrapping `compact_data`, `cleanup_old_data`, `vacuum_analyze`
- [ ] Create `audit/tasks.py` with `cleanup_audit_log` task
- [ ] Add Beat schedule entries (3 AM staggered: compact tier2 → tier3 → cleanup → vacuum → audit)
- [ ] Ensure tasks use **same logic** as management commands (import and call, or refactor to shared module)
- [ ] Deploy, start `celery-maintenance@2` (second worker for parallelism)
- [ ] **Monitor first 3–5 runs:** Check logs, row counts, timing, VACUUM completion
- [ ] **Once verified:** Disable `/etc/cron.d/monitoring-data-cleanup`
- [ ] **Keep `backup_db.sh` as cron** (pg_dump doesn't need Celery)

**Rollback:** Re-enable cron, stop maintenance workers.

---

### Phase 4: Async Ingest (High Impact, Requires Care)
This is the **most critical migration** — moves CPU/IO load off Gunicorn workers.

- [ ] Create `metrics_app/tasks.py:process_ingest_payload(rig_uuid, payload_dict, user_id, api_key_id)`
  - Move the **entire `process_ingest` logic** into this task
  - Task must be **idempotent**: use `update_or_create` on `MetricSnapshot` with `(rig_uuid, schema_version, timestamp)`
  - Return `{status: 'accepted', snapshot_id: ...}` or `{status: 'duplicate'}` / `{status: 'error', message: ...}`
- [ ] Modify `IngestView.post()`:
  ```python
  def post(self, request):
      # Validate auth, rig ownership, timestamp sanity (keep these synchronous — fast)
      # Serialize payload to JSON-serializable dict
      task = process_ingest_payload.delay(rig_uuid, payload_dict, user.id, api_key.id)
      return Response({'status': 'accepted', 'task_id': task.id}, status=202)
  ```
- [ ] Agent **no change required** — still POSTs to same endpoint, gets 202 instead of 200/201
- [ ] Deploy **1–8 ingest workers** (`celery-ingest@1..8`)
- [ ] **Load test:** Simulate 1000 rigs/minute, verify queue depth, latency, worker CPU
- [ ] **Monitor:** Gunicorn worker count can now be **reduced** (e.g., from 8 to 2) since ingest is offloaded

**Risks & Mitigations:**

| Risk | Mitigation |
|------|------------|
| Duplicate payloads (agent retry) | Idempotency key = `(rig_uuid, schema_version, timestamp)`; `update_or_create` handles |
| Task loss on worker crash | `acks_late=True` + `task_reject_on_worker_lost=True` on ingest task |
| Redis outage | Graceful degradation: if broker down, fall back to synchronous processing in view |
| Result backend growth | `CELERY_RESULT_EXPIRES=86400`; periodic cleanup of `django_celery_results_taskresult` |

**Rollback:** Revert `IngestView.post()` to synchronous `process_ingest()`, stop ingest workers.

---

### Phase 5: Future Enhancements (Post-Migration)
- [ ] **Alerts queue + tasks:** Rig down → email/webhook/PagerDuty
- [ ] **Reports queue + tasks:** Scheduled PDF/CSV generation, billing calculations
- [ ] **Chart data precomputation:** Celery task to warm cache for popular dashboards
- [ ] **Predictive analysis:** ML-based anomaly detection on GPU temps, power trends
- [ ] **Multi-rig agent coordination:** Fleet-wide commands via Celery (reboot, update, config push)

---

## 5. Potential Improvements Enabled by Celery

### 5.1 Synchronous Operations That Benefit from Async

| Operation | Current | Async Benefit |
|-----------|---------|---------------|
| **Telemetry ingest** | Synchronous in Gunicorn (200–800ms) | **202 Accepted in <50ms**; Gunicorn free for dashboard |
| **Rig enrollment (first payload)** | Synchronous DB writes | Offloaded; faster agent onboarding |
| **Chart data cache warm** | On-demand (first user waits) | Precompute via periodic task |
| **Report generation** | N/A (not implemented) | Background PDF/CSV, email when ready |
| **Bulk rig operations** | Synchronous admin actions | Async with progress tracking |
| **Agent update orchestration** | N/A | Coordinated fleet rollout via tasks |

### 5.2 New Celery Task Ideas

| Task | Queue | Trigger | Description |
|------|-------|---------|-------------|
| `aggregate_hourly_metrics` | `maintenance` | Hourly (Beat) | Pre-aggregate for 30d charts |
| `detect_anomalies` | `alerts` | Per-ingest or 5-min | ML/statistical anomaly detection on GPU temp, power, fan |
| `send_rig_down_alert` | `alerts` | Event-driven | Email/webhook when rig transitions to OFFLINE |
| `generate_fleet_report` | `reports` | Daily/Weekly (Beat) | PDF fleet health summary |
| `calculate_electricity_cost` | `reports` | Daily (Beat) | Per-rig, per-farm cost from power readings |
| `warm_dashboard_cache` | `default` | Every 5 min (Beat) | Pre-fetch chart data for active rigs |
| `sync_rig_tags_to_agents` | `default` | On tag change | Push tag updates to agents for filtering |

---

## 6. Risks and Edge Cases

### 6.1 Retries and Idempotency

| Task | Idempotency Strategy | Retry Policy |
|------|---------------------|--------------|
| `process_ingest_payload` | Natural key: `(rig_uuid, schema_version, timestamp)` + `update_or_create` | `autoretry_for=(Exception,)`, `retry_backoff=True`, `retry_kwargs={'max_retries': 3}` |
| `update_rig_status` | Idempotent by design (status transitions only forward) | No retry needed (runs every 2 min) |
| `compact_data` | **Not idempotent** — deletes then inserts. Use **advisory lock** | `max_retries=0` (do not retry; alert on failure) |
| `cleanup_old_data` | Idempotent (DELETE WHERE timestamp < X) | `max_retries=1` |
| `vacuum_analyze` | Idempotent | `max_retries=0` |

**Advisory lock pattern for compaction:**
```python
from django.db import connection

LOCK_ID = 123456789  # Fixed per compaction phase

@task(bind=True, max_retries=0)
def compact_data(self, phase, **kwargs):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
        locked = cursor.fetchone()[0]
        if not locked:
            raise self.retry(exc=Exception("Compaction already running"), countdown=300)
    try:
        # ... run compaction ...
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])
```

### 6.2 Duplicate Task Execution

- **Beat scheduler:** Single instance (enforced by systemd + Redis lock). Use `django-celery-beat` with `DatabaseScheduler` — only one beat process holds the scheduler lock.
- **Worker concurrency:** Set `--max-tasks-per-child` to recycle workers and prevent memory leaks.
- **Task deduplication:** For ingest, the natural key prevents duplicate snapshots. For maintenance tasks, advisory locks prevent concurrent runs.

### 6.3 Race Conditions

| Scenario | Risk | Mitigation |
|----------|------|------------|
| Two compaction workers run same phase | Data corruption, duplicate rows | Advisory lock (see above) |
| Ingest task + compaction on same rig | FK violation, deadlock | Compaction only touches data >1 day old; ingest is <1 min. Low overlap. Use `SELECT ... FOR SKIP LOCKED` if needed. |
| Rig status update + agent payload | `last_seen` update race | `update_rig_status` only reads `last_seen`; agent writes it. No conflict. |

### 6.4 Worker Failures

- **Graceful shutdown:** `SIGTERM` → worker finishes current task (up to `--time-limit`), then exits. Systemd `Restart=on-failure` restarts it.
- **Task acknowledgment:** Use `acks_late=True` for ingest tasks so redelivery happens if worker dies mid-task.
- **Visibility timeout:** Set `CELERY_TASK_VISIBILITY_TIMEOUT = 3600` (1 hour) to cover long compaction tasks.

### 6.5 Redis Outages

| Failure Mode | Impact | Mitigation |
|--------------|--------|------------|
| Redis down | No new tasks queued; beat can't schedule; workers idle | **Circuit breaker in view:** if `broker.ping()` fails, fall back to synchronous `process_ingest()` |
| Redis memory full | Broker rejects publishes | `maxmemory-policy allkeys-lru`; monitor `used_memory` |
| Redis network partition | Split brain (two beats) | Redis Sentinel for HA; only one beat elected |

### 6.6 Database Transactions

- **Ingest task:** Runs in its own transaction (Django default). If task fails, rollback is automatic.
- **Compaction:** Uses **autocommit** mode for `VACUUM` and raw SQL. Each batch is a transaction. Advisory lock serializes.
- **Cleanup:** Batched DELETEs, each batch autocommit. Safe.
- **Celery result backend (PostgreSQL):** Writes task results in separate transaction. Does not block task DB work.

### 6.7 Graceful Shutdown

```python
# In celery.py
import signal
from celery.signals import worker_shutting_down

@worker_shutting_down.connect
def shutdown_handler(sig, how, exitcode, **kwargs):
    logger.info(f"Worker shutting down: sig={sig}, how={how}")
    # Finish current task, don't accept new ones
```

Systemd `ExecStop=/bin/kill -TERM $MAINPID` sends SIGTERM. Celery handles it gracefully.

### 6.8 Scaling to Many Reporting Rigs

| Rig Count | Ingest Workers | Redis | DB Connections | Notes |
|-----------|----------------|-------|----------------|-------|
| 100 | 2 | Single | 20–30 | Baseline |
| 500 | 4 | Single | 40–60 | Monitor queue depth |
| 1000 | 6–8 | Sentinel HA | 80–120 | Consider read replica for chart queries |
| 5000 | 16+ | Cluster | 200+ | Partition ingest queue by rig hash; PgBouncer required |

**Horizontal scaling:** Add more `celery-ingest@N` workers. Queue depth metric (`celery -A gpu_monitor inspect active_queues`) drives autoscaling.

---

## 7. Configuration Reference

### 7.1 Environment Variables (Add to `.env`)

```bash
# Celery
CELERY_BROKER_URL=redis://localhost:***@.service.d/override.conf`:
```ini
[Service]
Environment="CELERY_WORKER_CONCURRENCY=4"
Environment="CELERY_WORKER_PREFETCH_MULTIPLIER=1"
```

---

## 8. Monitoring and Observability

### 8.1 Key Metrics to Watch

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| `celery_queue_length{queue="ingest"}` | Prometheus exporter | >1000 for 5 min |
| `celery_task_duration_seconds{task="process_ingest_payload"}` | Prometheus | p99 > 10s |
| `celery_worker_count` | Prometheus | < expected (e.g., <4 for ingest) |
| `redis_connected_clients` | Redis exporter | >80% maxclients |
| `celery_task_failed_total` | Prometheus | >0 in 1h |

### 8.2 Flower (Optional Web UI)

```bash
pip install flower
celery -A gpu_monitor flower --port=5555
```
Expose via Nginx with auth for live task monitoring.

---

## 9. Rollback Plan

| Phase | Rollback Action |
|-------|-----------------|
| 0 (Infra) | Stop systemd services, remove packages |
| 1 (Beat only) | Stop beat, re-enable all cron jobs |
| 2 (Rig status) | Stop maintenance workers, uncomment rig-status cron |
| 3 (Maintenance) | Stop maintenance workers, uncomment data-cleanup cron |
| 4 (Async ingest) | Revert `IngestView.post()` to synchronous, stop ingest workers |

**All phases are independently reversible.** No database schema changes required for Phases 0–3. Phase 4 adds no migrations either (task logic moved, not changed).

---

## 10. Appendix: Task Signatures

### `rigs/tasks.py`
```python
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from rigs.models import Rig
from metrics_app.models import RigStatusEvent

@shared_task(bind=True, queue='maintenance', priority=5)
def update_rig_status(self):
    now = timezone.now()
    stale_threshold = now - timedelta(minutes=2)
    offline_threshold = now - timedelta(minutes=10)

    stale_count = 0
    for rig in Rig.objects.filter(status=Rig.Status.ONLINE, last_seen__lt=stale_threshold, last_seen__gte=offline_threshold):
        rig.status = Rig.Status.STALE
        rig.save(update_fields=['status'])
        RigStatusEvent.objects.create(rig_uuid=str(rig.uuid), status=Rig.Status.STALE, previous_status=Rig.Status.ONLINE)
        stale_count += 1

    offline_count = 0
    for rig in Rig.objects.filter(last_seen__lt=offline_threshold).exclude(status=Rig.Status.OFFLINE):
        old = rig.status
        rig.status = Rig.Status.OFFLINE
        rig.save(update_fields=['status'])
        RigStatusEvent.objects.create(rig_uuid=str(rig.uuid), status=Rig.Status.OFFLINE, previous_status=old)
        offline_count += 1

    return {'stale': stale_count, 'offline': offline_count}
```

### `metrics_app/tasks.py` (key tasks)
```python
from celery import shared_task
from django.core.management import call_command
from django.db import connection

@shared_task(bind=True, queue='maintenance', priority=3, max_retries=0)
def compact_data(self, phase='all', verbose=False, days=31):
    lock_id = 1000 + {'tier2': 1, 'tier3': 2}.get(phase, 0)
    with connection.cursor() as c:
        c.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
        if not c.fetchone()[0]:
            raise self.retry(exc=Exception("Lock held"), countdown=300)
    try:
        call_command('compact_data', phase=phase, verbose=verbose, days=days)
    finally:
        with connection.cursor() as c:
            c.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
    return {'phase': phase, 'status': 'done'}

@shared_task(bind=True, queue='maintenance', priority=2)
def cleanup_old_data(self, days=31, verbose=False):
    call_command('cleanup_old_data', days=days, verbose=verbose)
    return {'days': days, 'status': 'done'}

@shared_task(bind=True, queue='maintenance', priority=1)
def vacuum_analyze(self):
    tables = [
        'metrics_gpumetric', 'metrics_storagemetric', 'metrics_networkmetric',
        'metrics_gpu_process', 'metrics_power_reading', 'metrics_metricsnapshot',
    ]
    for table in tables:
        with connection.cursor() as c:
            c.execute(f'VACUUM ANALYZE {table}')
    return {'tables': len(tables), 'status': 'done'}

@shared_task(bind=True, queue='ingest', priority=9, acks_late=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def process_ingest_payload(self, rig_uuid, payload_dict, user_id, api_key_id):
    from metrics_app.serializers import process_ingest
    from rigs.models import Rig
    from accounts.models import ApiKey
    
    rig = Rig.objects.get(uuid=rig_uuid)
    api_key = ApiKey.objects.get(id=api_key_id)
    user = api_key.user
    
    result, status = process_ingest(rig_uuid, payload_dict, user.id, rig=rig, enrolled_by_key_changed=False)
    return {'status': result.get('status'), 'snapshot_id': result.get('snapshot_id')}
```

---

## 11. Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-07 | Keep `pg_dump` backup as cron | No Django ORM needed; cron is simpler and more reliable for this |
| 2026-07 | Keep `VACUUM ANALYZE` as Celery task (not cron) | Unified scheduling in Beat; visibility in Flower; retry on failure |
| 2026-07 | Use `django-celery-beat` DatabaseScheduler | Persists schedule in DB; survives restarts; admin UI for schedule changes |
| 2026-07 | Use `django-celery-results` with PostgreSQL | Durable task results; queryable for debugging; no extra Redis memory |
| 2026-07 | Separate `ingest` queue with dedicated workers | Protects web responsiveness; independent scaling |
| 2026-07 | Advisory locks for compaction | Prevents concurrent runs without application-level coordination |

---

## 12. Next Steps for Repository Owner

1. **Review this plan** — confirm queue architecture, worker counts, Phase ordering
2. **Provision Redis** — single instance for dev, Sentinel/Cluster for prod
3. **Implement Phase 0** — infrastructure only, verify Celery cluster health
4. **Execute Phase 1** — beat + default worker, no cron changes
5. **Iterate through Phases 2–4** — one at a time, with 24–48h soak each
6. **Monitor metrics** — queue depth, task latency, worker health
7. **Plan Phase 5** — alerts, reports, predictive features

---

**Document Status:** Ready for implementation. No code modified in this analysis.
