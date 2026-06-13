# Possible Future Work: TimescaleDB Migration

> **Status:** Future consideration — NOT currently implemented. The current system uses plain PostgreSQL with custom compaction/retention.
>
> **When to reconsider:** When the platform grows to 10,000+ rigs, or when dashboard chart queries become too slow (currently ~100-500ms per query, acceptable for current scale).

---

## Table of Contents

1. [Current State](#1-current-state)
2. [Why TimescaleDB](#2-why-timescaledb)
3. [What Already Exists (Code Artifacts)](#3-what-already-exists-code-artifacts)
4. [Migration Plan — Detailed](#4-migration-plan--detailed)
   - [Phase 0: Prerequisites](#phase-0-prerequisites)
   - [Phase 1: Install and Configure TimescaleDB](#phase-1-install-and-configure-timescaledb)
   - [Phase 2: Convert Tables to Hypertables](#phase-2-convert-tables-to-hypertables)
   - [Phase 3: Create Continuous Aggregates](#phase-3-create-continuous-aggregates)
   - [Phase 4: Create Retention Policies](#phase-4-create-retention-policies)
   - [Phase 5: Update Chart Queries](#phase-5-update-chart-queries)
   - [Phase 6: Decommission Old Retention System](#phase-6-decommission-old-retention-system)
   - [Phase 7: Update Agent Install Script](#phase-7-update-agent-install-script)
5. [Complete SQL Reference](#5-complete-sql-reference)
6. [Files That Need Changes](#6-files-that-need-changes)
7. [Rollback Plan](#7-rollback-plan)
8. [Storage & Performance Estimates](#8-storage--performance-estimates)

---

## 1. Current State

### Database: PostgreSQL 16 (plain, no TimescaleDB)

**Confirmed by production runtime check:**
- `pg_extension` only contains `plpgsql` — TimescaleDB extension is NOT installed
- `dpkg -l | grep timescale` returns nothing — package not installed
- All metric tables are standard PostgreSQL tables with B-tree indexes
- No hypertables, no continuous aggregates, no compression policies

### Active Data Retention System

Two Django management commands handle retention:

| Command | File | What It Does | SQL Used |
|---------|------|-------------|----------|
| `compact_data` | `metrics_app/management/commands/compact_data.py` | Aggregates per-minute data into 1-hour buckets for data older than 1 day | Pure PostgreSQL: `date_trunc('hour', timestamp)`, `AVG()`, `SUM()`, `MAX()`, `ARRAY_AGG()`, temp tables |
| `cleanup_old_data` | `metrics_app/management/commands/cleanup_old_data.py` | Deletes data older than 31 days in 10K-row batches | Pure PostgreSQL: `DELETE ... WHERE timestamp < cutoff LIMIT 10000` |

**Cron schedule:** `data_retention.sh` runs daily at 3 AM via `/etc/cron.d/monitoring-data-cleanup`.

### Current Table Schema (All Plain PostgreSQL)

```
metrics_metricsnapshot       ← parent table (BigAutoField PK)
  ├── metrics_gpumetric       ← child (FK to snapshot)
  ├── metrics_gpu_process     ← child (FK to snapshot)
  ├── metrics_storagemetric   ← child (FK to snapshot)
  ├── metrics_networkmetric   ← child (FK to snapshot)
  ├── metrics_dockercontainermetric  ← independent (no FK)
  ├── metrics_latest_docker_container ← independent (latest snapshot, delete-before-insert)
  ├── metrics_latest_snapshot  ← independent (denormalized cache, PK = rig_uuid)
  └── metrics_rig_status_event ← independent (status transitions)
```

### Current Chart Queries

`ChartDataView` (`metrics_app/views.py` line 173-350):
- Uses `TruncHour`/`TruncMinute` + `Avg`/`Sum` from Django ORM
- Queries raw metric tables directly
- 1-minute buckets for 24h range, 1-hour buckets for 7d/30d ranges
- Multi-GPU, multi-disk, multi-interface support via `gpu_index`, `device`, `interface` filters
- Byte conversion: `BYTE_TO_GB` (÷ 1024³), `BYTE_TO_MB` (÷ 1024²)

### Unique Constraints (Important for Hypertable Migration)

All metric tables use `unique_together` which conflict with TimescaleDB hypertable requirements:

| Table | Current unique_together | Conflict? |
|-------|----------------------|-----------|
| `metrics_metricsnapshot` | `(rig_uuid, schema_version, timestamp)` | ✅ Yes — PK is `id BigAutoField` |
| `metrics_gpumetric` | `(rig_uuid, timestamp, gpu_index)` | ✅ Yes — includes `snapshot_id` FK |
| `metrics_storagemetric` | `(rig_uuid, timestamp, device)` | ✅ Yes — includes `snapshot_id` FK |
| `metrics_networkmetric` | `(rig_uuid, timestamp, interface)` | ✅ Yes — includes `snapshot_id` FK |
| `metrics_dockercontainermetric` | `(rig_uuid, timestamp, name)` | ✅ Yes |
| `metrics_gpu_process` | `(rig_uuid, timestamp, gpu_index, pid)` | ✅ Yes |

**TimescaleDB hypertable requirement:** The partition key (`timestamp`) must be part of any unique constraint or primary key. These constraints will need to be restructured.

---

## 2. Why TimescaleDB

### Problems with Current Approach at Scale

| Problem | Current Impact | At 10,000 Rigs |
|---------|---------------|-----------------|
| Chart query speed | ~100-500ms per query (acceptable) | ~5-30s per query (unacceptable) |
| Storage growth | ~9 GB/month after compaction | ~900 GB/month raw, ~90 GB/month compacted |
| Maintenance | Manual cron scripts, error-prone | Automatic (built-in policies) |
| Batch deletion impact | Brief locks during cleanup | No locks (automatic chunk dropping) |

### TimescaleDB Benefits for This Project

1. **Hypertables:** Automatic time-based partitioning (1-day chunks). Queries only hit relevant chunks.
2. **Continuous Aggregates:** Pre-computed materialized views that auto-refresh. Charts hit aggregated data directly instead of scanning raw rows.
3. **Compression:** 90-95% additional storage reduction on old chunks. Compressed chunks are still queryable.
4. **Retention Policies:** Automatic `DROP CHUNK` via `add_retention_policy` — no custom scripts.
5. **Real-time Aggregation:** Combines pre-aggregate + fresh raw data automatically for up-to-the-minute accuracy.

### Comparison Matrix

| Criteria | Current (compact_data) | TimescaleDB |
|----------|----------------------|-------------|
| Setup complexity | Low (plain PostgreSQL) | Medium (new extension) |
| Maintenance | Manual cron (2 scripts) | Automatic (built-in) |
| Chart query speed | 100-500ms (runtime agg) | <50ms (pre-computed) |
| Storage at 1,000 rigs | ~9 GB/month | ~9 GB/month |
| Storage at 10,000 rigs | ~900 GB/month | ~90 GB/month |
| Portability | Any PostgreSQL hosting | Limited (requires extension) |

---

## 3. What Already Exists (Code Artifacts)

### 3.1 — `setup_timescale.py` (Written but Never Executed)

**File:** `gpu_monitor/metrics_app/management/commands/setup_timescale.py`

This management command was written as a one-time setup script. It contains:

```python
# Convert metrics_metricsnapshot to hypertable
cursor.execute("""
    SELECT create_hypertable(
        'metrics_metricsnapshot',
        'timestamp',
        if_not_exists => TRUE,
        chunk_time_interval => INTERVAL '1 day'
    );
""")

# Retention policy: drop raw data after 7 days
cursor.execute("""
    SELECT add_retention_policy(
        'metrics_metricsnapshot',
        drop_after => INTERVAL '7 days',
        if_not_exists => TRUE
    );
""")

# Continuous aggregate: hourly
cursor.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_hourly_agg
    WITH (timescaledb.continuous) AS
    SELECT
        rig_uuid,
        time_bucket('1 hour', timestamp) AS bucket,
        AVG(cpu_utilization_pct) AS avg_cpu_util,
        MAX(cpu_utilization_pct) AS max_cpu_util,
        AVG(cpu_temp_c) AS avg_cpu_temp,
        MAX(cpu_temp_c) AS max_cpu_temp,
        AVG(mem_used_bytes) AS avg_mem_used,
        MAX(mem_used_bytes) AS max_mem_used
    FROM metrics_metricsnapshot
    GROUP BY rig_uuid, bucket
    WITH NO DATA;
""")

# Refresh policy for hourly aggregate
cursor.execute("""
    SELECT add_continuous_aggregate_policy(
        'metrics_hourly_agg',
        start_offset => INTERVAL '3 hours',
        end_offset => INTERVAL '1 hour',
        schedule_interval => INTERVAL '1 hour',
        if_not_exists => TRUE
    );
""")
```

**Status:** This command is **never called** during installation. `server_install.sh` only prints it as a manual post-install hint (line 179). It also only covers `metrics_metricsnapshot` — not the child tables (GPU, storage, network, Docker) that charts actually query.

### 3.2 — `server_install.sh` TimescaleDB References

**File:** `gpu_monitor/deploy/server_install.sh`

The following lines reference TimescaleDB:

| Line | Code | Impact |
|------|------|--------|
| 20 | `apt install ... timescaledb-2-postgresql-16 ...` | Installs TimescaleDB package |
| 24 | `timescaledb-tune --quiet 2>/dev/null \|\| true` | Auto-tunes PostgreSQL config for TS |
| 36 | `CREATE EXTENSION IF NOT EXISTS timescaledb;` | Creates the database extension |
| 178-179 | Echo "Set up TimescaleDB hypertables:" + command | Manual post-install instruction |

### 3.3 — `TIMESCALEDB_VS_OUR_APPROACH.md` Analysis

**File:** `docs/TIMESCALEDB_VS_OUR_APPROACH.md`

Contains a thorough comparison of both approaches with a verdict: **keep current approach for ≤1,000 rigs**, reconsider at 10,000+ rigs. Includes a migration path outline:
1. Install TimescaleDB extension
2. Convert tables to hypertables
3. Create continuous aggregates matching ChartDataView queries
4. Replace compact_data/cleanup_old_data with TimescaleDB retention policies
5. Update ChartDataView to query continuous aggregates

### 3.4 — `DATA_RETENTION_PLAN.md`

**File:** `docs/DATA_RETENTION_PLAN.md`

Documents the current tiered compaction strategy:
- Tier 1: Raw data (0-1 day) — all per-minute data
- Tier 2: 1-hour buckets (1-31 days) — 60× reduction
- Tier 3: Delete (31+ days)

Storage projections (updated with actual measurements):
- Per rig: ~15.7 MB/day raw → ~23.6 MB/month with compaction (31-day retention)
- 1,000 rigs: ~487 GB/month raw → ~23 GB/month compacted (95% reduction)
- 10,000 rigs: ~4.6 TB/month raw → ~720 GB/month compacted

**Note:** Earlier projections (4.7 MB/day/rig, 146 GB/month for 1K rigs) were estimates before full deployment. Actual measurements from 100 rigs over 10 days show ~3.3x higher storage due to larger row sizes from JSON fields and higher Docker container metric volume.

---

## 4. Migration Plan — Detailed

### Phase 0: Prerequisites

**Scale threshold:** Consider migration when:
- Rig count exceeds 5,000-10,000
- Chart queries consistently exceed 1 second
- Database size exceeds 500 GB
- Maintenance burden of compaction scripts becomes problematic

**Pre-migration checklist:**
- [ ] Full database backup (`pg_dump -Fc gpu_monitor`)
- [ ] Test migration on a staging copy first
- [ ] Verify TimescaleDB compatibility with hosting provider (not available on all managed PostgreSQL services)
- [ ] Schedule maintenance window (hypertable conversion requires table locks)

### Phase 1: Install and Configure TimescaleDB

**Already handled by `server_install.sh`** for new deployments. For existing deployments:

```bash
# 1. Install TimescaleDB package
sudo apt install -y timescaledb-2-postgresql-16

# 2. Tune PostgreSQL configuration
sudo timescaledb-tune --quiet
sudo systemctl restart postgresql

# 3. Create the extension
sudo -u postgres psql -d gpu_monitor -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"

# 4. Verify
sudo -u postgres psql -d gpu_monitor -c "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';"
```

**PostgreSQL config changes** (`timescaledb-tune` modifies `postgresql.conf`):
- Increases `shared_preload_libraries` to include `timescaledb`
- Adjusts `work_mem`, `maintenance_work_mem`, `effective_cache_size`
- Sets `max_worker_processes`, `max_parallel_workers`

### Phase 2: Convert Tables to Hypertables

**Critical issue:** Current tables have `BigAutoField` primary keys and `unique_together` constraints that don't include `timestamp` as the leading column. TimescaleDB requires the partition key (`timestamp`) to be part of any unique constraint.

**Migration strategy for each table:**

#### 2a. `metrics_metricsnapshot` (parent table)

```sql
-- Step 1: Drop the old unique constraint (it doesn't include timestamp as leading column)
ALTER TABLE metrics_metricsnapshot
    DROP CONSTRAINT metrics_metricsnapshot_rig_uuid_schema_version_timestamp_xxxxx_uniq;

-- Step 2: Create hypertable (converts the table)
SELECT create_hypertable(
    'metrics_metricsnapshot',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Step 3: Add back a TimescaleDB-compatible unique constraint
-- Note: timestamp must be part of the unique constraint
ALTER TABLE metrics_metricsnapshot
    ADD CONSTRAINT metrics_metricsnapshot_unique
    UNIQUE (rig_uuid, timestamp, schema_version, id);
```

#### 2b. Child tables (GPUMetric, StorageMetric, NetworkMetric, GPUProcessMetric)

```sql
-- GPUMetric
ALTER TABLE metrics_gpumetric
    DROP CONSTRAINT metrics_gpumetric_rig_uuid_timestamp_gpu_index_xxxxx_uniq;
SELECT create_hypertable('metrics_gpumetric', 'timestamp', chunk_time_interval => INTERVAL '1 day');
ALTER TABLE metrics_gpumetric
    ADD CONSTRAINT metrics_gpumetric_unique
    UNIQUE (rig_uuid, timestamp, gpu_index, id);

-- StorageMetric
ALTER TABLE metrics_storagemetric
    DROP CONSTRAINT metrics_storagemetric_rig_uuid_timestamp_device_xxxxx_uniq;
SELECT create_hypertable('metrics_storagemetric', 'timestamp', chunk_time_interval => INTERVAL '1 day');
ALTER TABLE metrics_storagemetric
    ADD CONSTRAINT metrics_storagemetric_unique
    UNIQUE (rig_uuid, timestamp, device, id);

-- NetworkMetric
ALTER TABLE metrics_networkmetric
    DROP CONSTRAINT metrics_networkmetric_rig_uuid_timestamp_interface_xxxxx_uniq;
SELECT create_hypertable('metrics_networkmetric', 'timestamp', chunk_time_interval => INTERVAL '1 day');
ALTER TABLE metrics_networkmetric
    ADD CONSTRAINT metrics_networkmetric_unique
    UNIQUE (rig_uuid, timestamp, interface, id);

-- GPUProcessMetric
ALTER TABLE metrics_gpu_process
    DROP CONSTRAINT metrics_gpu_process_rig_uuid_timestamp_gpu_index_pid_xxxxx_uniq;
SELECT create_hypertable('metrics_gpu_process', 'timestamp', chunk_time_interval => INTERVAL '1 day');
ALTER TABLE metrics_gpu_process
    ADD CONSTRAINT metrics_gpu_process_unique
    UNIQUE (rig_uuid, timestamp, gpu_index, pid, id);
```

#### 2c. `metrics_dockercontainermetric` (independent, no FK)

```sql
ALTER TABLE metrics_dockercontainermetric
    DROP CONSTRAINT metrics_dockercontainermetric_rig_uuid_timestamp_name_xxxxx_uniq;
SELECT create_hypertable('metrics_dockercontainermetric', 'timestamp', chunk_time_interval => INTERVAL '1 day');
ALTER TABLE metrics_dockercontainermetric
    ADD CONSTRAINT metrics_dockercontainermetric_unique
    UNIQUE (rig_uuid, timestamp, name, id);
```

#### 2d. Tables that should NOT be converted

| Table | Reason |
|-------|--------|
| `metrics_latest_snapshot` | Single row per rig (PK = `rig_uuid`), no time-series |
| `metrics_latest_docker_container` | Latest snapshot only, delete-before-insert pattern |
| `metrics_rig_status_event` | Low volume, event-based, not high-frequency metrics |

### Phase 3: Create Continuous Aggregates

**Goal:** Replace the runtime aggregation in `ChartDataView` with pre-computed continuous aggregates.

#### 3a. CPU/Memory Hourly Aggregate (replaces MetricSnapshot chart queries)

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_snapshot_hourly
WITH (timescaledb.continuous) AS
SELECT
    rig_uuid,
    time_bucket('1 hour', timestamp) AS bucket,
    AVG(cpu_utilization_pct) AS avg_cpu_util,
    MAX(cpu_utilization_pct) AS max_cpu_util,
    AVG(cpu_temp_c) AS avg_cpu_temp,
    MAX(cpu_temp_c) AS max_cpu_temp,
    AVG(mem_used_bytes) AS avg_mem_used,
    MAX(mem_used_bytes) AS max_mem_used,
    AVG(mem_free_bytes) AS avg_mem_free,
    AVG(mem_cached_bytes) AS avg_mem_cached,
    AVG(swap_used_bytes) AS avg_swap_used,
    MAX(swap_total_bytes) AS max_swap_total,
    SUM(error_count) AS sum_error_count
FROM metrics_metricsnapshot
GROUP BY rig_uuid, bucket
WITH NO DATA;

-- Refresh policy: every hour, processing last 3 hours of data
SELECT add_continuous_aggregate_policy(
    'metrics_snapshot_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);
```

#### 3b. GPU Hourly Aggregate (replaces GPUMetric chart queries)

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_gpu_hourly
WITH (timescaledb.continuous) AS
SELECT
    rig_uuid,
    gpu_index,
    time_bucket('1 hour', timestamp) AS bucket,
    AVG(gpu_util_pct) AS avg_gpu_util,
    MAX(gpu_util_pct) AS max_gpu_util,
    AVG(gpu_temp_c) AS avg_gpu_temp,
    MAX(gpu_temp_c) AS max_gpu_temp,
    AVG(fan_speed_pct) AS avg_fan_speed,
    AVG(mem_used_mb) AS avg_mem_used_mb,
    MAX(mem_total_mb) AS max_mem_total_mb,
    AVG(power_draw_w) AS avg_power_draw_w,
    MAX(power_limit_w) AS max_power_limit_w
FROM metrics_gpumetric
GROUP BY rig_uuid, gpu_index, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'metrics_gpu_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);
```

#### 3c. Storage Hourly Aggregate

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_storage_hourly
WITH (timescaledb.continuous) AS
SELECT
    rig_uuid,
    device,
    time_bucket('1 hour', timestamp) AS bucket,
    AVG(usage_pct) AS avg_usage_pct,
    MAX(usage_pct) AS max_usage_pct,
    AVG(temp_c) AS avg_temp_c,
    MAX(capacity_bytes) AS max_capacity_bytes
FROM metrics_storagemetric
GROUP BY rig_uuid, device, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'metrics_storage_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);
```

#### 3d. Network Hourly Aggregate

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_network_hourly
WITH (timescaledb.continuous) AS
SELECT
    rig_uuid,
    interface,
    time_bucket('1 hour', timestamp) AS bucket,
    SUM(rx_bytes_delta) AS sum_rx_delta,
    SUM(tx_bytes_delta) AS sum_tx_delta,
    SUM(rx_errors) AS sum_rx_errors,
    SUM(tx_errors) AS sum_tx_errors,
    MAX(link_speed_mbps) AS max_link_speed
FROM metrics_networkmetric
GROUP BY rig_uuid, interface, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'metrics_network_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);
```

#### 3e. Docker Container Hourly Aggregate

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_docker_hourly
WITH (timescaledb.continuous) AS
SELECT
    rig_uuid,
    name,
    time_bucket('1 hour', timestamp) AS bucket,
    AVG(cpu_pct) AS avg_cpu_pct,
    MAX(cpu_pct) AS max_cpu_pct,
    AVG(mem_usage_bytes) AS avg_mem_usage,
    MAX(mem_usage_bytes) AS max_mem_usage
FROM metrics_dockercontainermetric
GROUP BY rig_uuid, name, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'metrics_docker_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);
```

### Phase 4: Create Retention Policies

**Replace `compact_data` + `cleanup_old_data` with TimescaleDB automatic policies:**

```sql
-- Drop raw data after 7 days (replaces cleanup_old_data --days=31)
-- Keep 7 days of raw data for 24h charts at 1-minute resolution
SELECT add_retention_policy(
    'metrics_metricsnapshot',
    drop_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

SELECT add_retention_policy(
    'metrics_gpumetric',
    drop_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

SELECT add_retention_policy(
    'metrics_storagemetric',
    drop_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

SELECT add_retention_policy(
    'metrics_networkmetric',
    drop_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

SELECT add_retention_policy(
    'metrics_dockercontainermetric',
    drop_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

SELECT add_retention_policy(
    'metrics_gpu_process',
    drop_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Drop continuous aggregates after 31 days (replaces cleanup_old_data --days=31)
SELECT add_retention_policy(
    'metrics_snapshot_hourly',
    drop_after => INTERVAL '31 days',
    if_not_exists => TRUE
);

SELECT add_retention_policy(
    'metrics_gpu_hourly',
    drop_after => INTERVAL '31 days',
    if_not_exists => TRUE
);

-- ... same for storage, network, docker hourly aggregates
```

**Note:** TimescaleDB retention policies use `DROP CHUNK` which is much faster than row-by-row `DELETE`. No batch processing needed.

### Phase 5: Update Chart Queries

**File:** `gpu_monitor/metrics_app/views.py` — `ChartDataView`

Replace Django ORM aggregation queries with continuous aggregate lookups:

```python
# BEFORE (current — runtime aggregation on raw tables):
data = list(
    MetricSnapshot.objects.filter(**base_filter)
    .annotate(bucket=trunc('timestamp'))
    .values('bucket')
    .annotate(val=agg_func(metric))
    .order_by('bucket')
)

# AFTER (TimescaleDB — query continuous aggregate):
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute("""
        SELECT bucket, avg_cpu_util
        FROM metrics_snapshot_hourly
        WHERE rig_uuid = %s
          AND bucket >= %s
          AND bucket <= %s
        ORDER BY bucket
    """, [str(uuid), start_bucket, end_bucket])
    data = cursor.fetchall()
```

**Affected metric groups in ChartDataView:**

| Metric Group | Current Query | New Query Target |
|-------------|---------------|-----------------|
| `SNAPSHOT_METRICS` (CPU, memory, swap) | `MetricSnapshot.objects.filter().annotate(TruncHour)` | `metrics_snapshot_hourly` |
| `GPU_METRICS` (temp, util, mem, power, fan) | `GPUMetric.objects.filter().annotate(TruncHour)` | `metrics_gpu_hourly` |
| `STORAGE_METRICS` (usage_pct) | `StorageMetric.objects.filter().annotate(TruncHour)` | `metrics_storage_hourly` |
| `net_*` (rx/tx delta, errors) | `NetworkMetric.objects.filter().annotate(TruncHour)` | `metrics_network_hourly` |
| `container_*` (cpu, mem) | `DockerContainerMetric.objects.filter().annotate(TruncHour)` | `metrics_docker_hourly` |
| `error_frequency` | `MetricSnapshot.objects.filter().annotate(Sum('error_count'))` | `metrics_snapshot_hourly.sum_error_count` |
| `cpu_load_avg` | Python-side processing of `cpu_load_avg_json` | New continuous aggregate needed |
| `uptime_s` | Python-side processing of `software_json` | New continuous aggregate needed |

### Phase 6: Decommission Old Retention System

After TimescaleDB retention policies are confirmed working:

1. **Remove cron job:**
   ```bash
   sudo rm /etc/cron.d/monitoring-data-cleanup
   ```

2. **Remove management commands:**
   ```bash
   rm gpu_monitor/metrics_app/management/commands/compact_data.py
   rm gpu_monitor/metrics_app/management/commands/cleanup_old_data.py
   ```

3. **Remove wrapper script:**
   ```bash
   sudo rm /opt/gpu_monitor/deploy/data_retention.sh
   ```

4. **Remove `setup_timescale.py`** (one-time command, no longer needed after migration):
   ```bash
   rm gpu_monitor/metrics_app/management/commands/setup_timescale.py
   ```

### Phase 7: Update Agent Install Script

**File:** `gpu_monitor/deploy/server_install.sh`

Remove or update TimescaleDB references:
- Line 20: Remove `timescaledb-2-postgresql-16` from apt install (or keep for new deployments)
- Line 24: Remove `timescaledb-tune` call
- Line 36: Remove `CREATE EXTENSION IF NOT EXISTS timescaledb`
- Lines 178-179: Remove the manual `setup_timescale` hint

---

## 5. Complete SQL Reference

### Hypertable Conversion (All Tables)

```sql
-- ============================================================
-- TIMESCALEDB MIGRATION — COMPLETE SQL SCRIPT
-- Run as: sudo -u postgres psql -d gpu_monitor
-- ============================================================

-- 0. Create extension (if not already done)
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 1. Convert parent table
ALTER TABLE metrics_metricsnapshot
    DROP CONSTRAINT IF EXISTS metrics_metricsnapshot_rig_uuid_schema_version_timestamp_uniq;
SELECT create_hypertable('metrics_metricsnapshot', 'timestamp',
    chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
ALTER TABLE metrics_metricsnapshot
    ADD CONSTRAINT metrics_snapshot_unique
    UNIQUE (rig_uuid, timestamp, schema_version, id);

-- 2. Convert child tables
ALTER TABLE metrics_gpumetric
    DROP CONSTRAINT IF EXISTS metrics_gpumetric_rig_uuid_timestamp_gpu_index_uniq;
SELECT create_hypertable('metrics_gpumetric', 'timestamp',
    chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
ALTER TABLE metrics_gpumetric
    ADD CONSTRAINT metrics_gpumetric_unique
    UNIQUE (rig_uuid, timestamp, gpu_index, id);

ALTER TABLE metrics_storagemetric
    DROP CONSTRAINT IF EXISTS metrics_storagemetric_rig_uuid_timestamp_device_uniq;
SELECT create_hypertable('metrics_storagemetric', 'timestamp',
    chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
ALTER TABLE metrics_storagemetric
    ADD CONSTRAINT metrics_storagemetric_unique
    UNIQUE (rig_uuid, timestamp, device, id);

ALTER TABLE metrics_networkmetric
    DROP CONSTRAINT IF EXISTS metrics_networkmetric_rig_uuid_timestamp_interface_uniq;
SELECT create_hypertable('metrics_networkmetric', 'timestamp',
    chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
ALTER TABLE metrics_networkmetric
    ADD CONSTRAINT metrics_networkmetric_unique
    UNIQUE (rig_uuid, timestamp, interface, id);

ALTER TABLE metrics_dockercontainermetric
    DROP CONSTRAINT IF EXISTS metrics_dockercontainermetric_rig_uuid_timestamp_name_uniq;
SELECT create_hypertable('metrics_dockercontainermetric', 'timestamp',
    chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
ALTER TABLE metrics_dockercontainermetric
    ADD CONSTRAINT metrics_dockercontainermetric_unique
    UNIQUE (rig_uuid, timestamp, name, id);

ALTER TABLE metrics_gpu_process
    DROP CONSTRAINT IF EXISTS metrics_gpu_process_rig_uuid_timestamp_gpu_index_pid_uniq;
SELECT create_hypertable('metrics_gpu_process', 'timestamp',
    chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
ALTER TABLE metrics_gpu_process
    ADD CONSTRAINT metrics_gpu_process_unique
    UNIQUE (rig_uuid, timestamp, gpu_index, pid, id);

-- 3. Create continuous aggregates (see Phase 3 above for full SQL)

-- 4. Create retention policies (see Phase 4 above for full SQL)

-- 5. Verify
SELECT hypertable_name, num_chunks
FROM timescaledb_information.hypertables
WHERE hypertable_schema = 'public';

SELECT view_name, materialization_hypertable_name
FROM timescaledb_information.continuous_aggregates;

SELECT policy_func, config
FROM timescaledb_information.jobs
WHERE application_name LIKE '%retention%';
```

---

## 6. Files That Need Changes

### To Modify During Migration

| File | Change |
|------|--------|
| `gpu_monitor/metrics_app/views.py` | `ChartDataView` — replace ORM aggregation with continuous aggregate queries |
| `gpu_monitor/metrics_app/models.py` | Update `Meta.unique_together` and `Meta.indexes` for hypertable compatibility |
| `gpu_monitor/metrics_app/migrations/` | New migration: drop old constraints, add new ones, create hypertables |
| `gpu_monitor/deploy/server_install.sh` | Remove or update TimescaleDB install references |

### To Remove After Migration

| File | Reason |
|------|--------|
| `gpu_monitor/metrics_app/management/commands/compact_data.py` | Replaced by TimescaleDB retention policies |
| `gpu_monitor/metrics_app/management/commands/cleanup_old_data.py` | Replaced by TimescaleDB retention policies |
| `gpu_monitor/metrics_app/management/commands/setup_timescale.py` | One-time setup, no longer needed |
| `gpu_monitor/deploy/data_retention.sh` | Replaced by TimescaleDB retention policies |
| `/etc/cron.d/monitoring-data-cleanup` | Replaced by TimescaleDB retention policies |

### To Keep Unchanged

| File | Reason |
|------|--------|
| `gpu_monitor/metrics_app/serializers.py` | Ingest pipeline writes to same tables regardless of TS |
| `gpu_monitor/metrics_app/models.py` (model definitions) | Same fields, just different table structure |
| `gpu_monitor/dashboard/views.py` | Live Metrics reads `LatestSnapshot` (not time-series) |
| `gpu_monitor/rigs/` | Rig management unchanged |
| `gpu_monitor/accounts/` | Auth unchanged |
| `agent/run.py`, `agent_windows/run.py` | Agents POST same payload format |

---

## 7. Rollback Plan

If TimescaleDB migration causes issues:

```sql
-- 1. Remove retention policies first
SELECT remove_retention_policy('metrics_metricsnapshot', if_exists => TRUE);
-- ... repeat for all tables

-- 2. Remove continuous aggregates
DROP MATERIALIZED VIEW IF EXISTS metrics_snapshot_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS metrics_gpu_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS metrics_storage_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS metrics_network_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS metrics_docker_hourly CASCADE;

-- 3. Convert hypertables back to regular tables
SELECT decompress_chunk(chunk, if_compressed => TRUE)
FROM timescaledb_information.chunks
WHERE hypertable_name = 'metrics_metricsnapshot';

-- Note: There is no direct "de-hypertable" function.
-- Full rollback requires: pg_dump data → drop table → recreate as regular table → restore data
-- This is why a full backup BEFORE migration is critical.
```

**Simpler rollback:** Restore from the pre-migration `pg_dump -Fc gpu_monitor` backup.

---

## 8. Storage & Performance Estimates

### Current System (Plain PostgreSQL + Compaction)

| Scale | Raw/Month | After Compaction | Chart Query |
|-------|-----------|-----------------|-------------|
| 100 rigs | 14.6 GB | 0.7 GB | ~50ms |
| 1,000 rigs | 146 GB | ~9 GB | ~100-500ms |
| 10,000 rigs | 1.46 TB | ~900 GB | ~5-30s |

### With TimescaleDB

| Scale | Raw/Month | After Compression + Retention | Chart Query |
|-------|-----------|-------------------------------|-------------|
| 100 rigs | 14.6 GB | 0.5 GB (7d raw + 24d compressed hourly) | ~10ms |
| 1,000 rigs | 146 GB | ~7 GB (7d raw + 24d compressed hourly) | ~20-50ms |
| 10,000 rigs | 1.46 TB | ~90 GB (7d raw + 24d compressed hourly) | ~50-100ms |

### Key Metrics

- **Chunk size:** 1 day (recommended for this data rate)
- **Continuous aggregate refresh:** Every hour, processing 3-hour window
- **Raw data retention:** 7 days (vs current 1 day before compaction)
- **Aggregate retention:** 31 days (same as current)
- **Compression ratio:** ~90-95% on chunks older than 1 day
- **Query improvement:** 10-100× faster chart queries (pre-computed vs runtime aggregation)

---

## Appendix A: Current `setup_timescale.py` Limitations

The existing `setup_timescale.py` command has these issues that would need fixing before use:

1. **Only covers `metrics_metricsnapshot`** — doesn't convert child tables (GPU, storage, network, Docker)
2. **No unique constraint handling** — doesn't drop/recreate constraints for hypertable compatibility
3. **Only one continuous aggregate** — only `metrics_hourly_agg` for CPU/memory, missing GPU/storage/network/Docker
4. **Retention policy only on parent** — doesn't set up retention on child tables
5. **No compression policy** — TimescaleDB compression is not configured
6. **No error handling for existing data** — `create_hypertable` will fail if table already has data and constraints are incompatible

---

## Appendix B: Useful TimescaleDB Queries for This Project

```sql
-- Check hypertable status
SELECT hypertable_name, num_chunks, compression_enabled
FROM timescaledb_information.hypertables;

-- Check chunk sizes
SELECT chunk_name, range_start, range_end, pg_size_pretty(total_bytes)
FROM chunks_detailed_size('metrics_metricsnapshot')
ORDER BY range_start DESC
LIMIT 10;

-- Check continuous aggregate status
SELECT view_name, materialization_hypertable_name,
       compression_enabled, refresh_lag
FROM timescaledb_information.continuous_aggregates;

-- Manual refresh of continuous aggregate
CALL refresh_continuous_aggregate('metrics_snapshot_hourly',
    NOW() - INTERVAL '3 hours', NOW());

-- Enable compression on hypertable (after 1 day)
ALTER TABLE metrics_metricsnapshot SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'rig_uuid',
    timescaledb.compress_orderby = 'timestamp DESC'
);

-- Compression policy: compress chunks older than 1 day
SELECT add_compression_policy('metrics_metricsnapshot',
    compress_after => INTERVAL '1 day',
    if_not_exists => TRUE);

-- Check compression stats
SELECT chunk_name, compression_status, before_compression_total_bytes,
       after_compression_total_bytes
FROM chunk_compression_stats('metrics_metricsnapshot');
```
