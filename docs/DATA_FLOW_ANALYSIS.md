# Data Flow Analysis — Complete Reference

## Principle
"Main source of data for numeric values that change over time should always be a dedicated database field that stores time series values. We should always save payload data there and read from there for both Live Metrics and charts."

**Status:** ✅ Principle followed. Every numeric value that changes over time is stored in a dedicated database field and read from that field for display and charts.

---

## Complete Data Model — Payload to DB Field Mapping

### MetricSnapshot (one row per rig per heartbeat)

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 1 | CPU utilization | `metrics.cpu.utilization_pct` | `cpu_utilization_pct` | FloatField | ✅ |
| 2 | CPU temperature | `metrics.cpu.temp_c` | `cpu_temp_c` | FloatField | ✅ |
| 3 | CPU load avg | `metrics.cpu.load_avg` | `cpu_load_avg_json` | JSONField[3] | ✅ |
| 4 | Memory total | `metrics.memory.total_bytes` | `mem_total_bytes` | BigIntegerField | ✅ |
| 5 | Memory used | `metrics.memory.used_bytes` | `mem_used_bytes` | BigIntegerField | ✅ |
| 6 | Memory free | `metrics.memory.free_bytes` | `mem_free_bytes` | BigIntegerField | ✅ |
| 7 | Memory cached | `metrics.memory.cached_bytes` | `mem_cached_bytes` | BigIntegerField | ✅ |
| 8 | Swap used | `metrics.memory.swap_used_bytes` | `swap_used_bytes` | BigIntegerField | ✅ |
| 9 | Swap total | `metrics.memory.swap_total_bytes` | `swap_total_bytes` | BigIntegerField | ✅ |
| 10 | Uptime | `software.uptime_s` | `software_json.uptime_s` | JSON | ✅ |
| 11 | Rig status | `rig.status` (view) | `status` | CharField | ✅ |
| 12 | Error count | `errors[]` length | `error_count` | PositiveIntegerField | ✅ |

### Rig (latest error text — updated in place, not per-snapshot)

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 48 | Latest errors | `errors[]` | `latest_errors_json` | JSONField | ✅ (latest only) |

### GPUMetric (one row per GPU per heartbeat)

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 14 | GPU utilization | `metrics.gpus[].gpu_util_pct` | `gpu_util_pct` | FloatField | ✅ |
| 15 | GPU temperature | `metrics.gpus[].temp_c` | `gpu_temp_c` | FloatField | ✅ |
| 16 | GPU fan speed | `metrics.gpus[].fan_speed_pct` | `fan_speed_pct` | FloatField | ✅ |
| 17 | GPU VRAM total | `metrics.gpus[].mem_total_mb` | `mem_total_mb` | IntegerField | ✅ |
| 18 | GPU VRAM used | `metrics.gpus[].mem_used_mb` | `mem_used_mb` | IntegerField | ✅ |
| 19 | GPU VRAM free | `metrics.gpus[].mem_free_mb` | `mem_free_mb` | IntegerField | ✅ |
| 20 | GPU VRAM util | `metrics.gpus[].mem_util_pct` | `mem_util_pct` | FloatField | ✅ |
| 21 | GPU power draw | `metrics.gpus[].power_draw_w` | `power_draw_w` | FloatField | ✅ |
| 22 | GPU power limit | `metrics.gpus[].power_limit_w` | `power_limit_w` | FloatField | ✅ |
| 23 | PCIe current gen | `metrics.gpus[].pcie_current_gen` | `pcie_current_gen` | PositiveSmallIntegerField | ✅ |
| 24 | PCIe max gen | `metrics.gpus[].pcie_max_gen` | `pcie_max_gen` | PositiveSmallIntegerField | ✅ |
| 25 | PCIe current width | `metrics.gpus[].pcie_current_width` | `pcie_current_width` | PositiveSmallIntegerField | ✅ |
| 26 | PCIe max width | `metrics.gpus[].pcie_max_width` | `pcie_max_width` | PositiveSmallIntegerField | ✅ |

### StorageMetric (one row per disk per heartbeat)

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 27 | Storage usage | `metrics.storage[].usage_pct` | `usage_pct` | FloatField | ✅ |
| 28 | Storage temp | `metrics.storage[].temp_c` | `temp_c` | FloatField | ✅ |

### NetworkMetric (one row per interface per heartbeat)

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 29 | Network RX bytes | `metrics.network[].rx_bytes` | `rx_bytes` | BigIntegerField | ✅ |
| 30 | Network TX bytes | `metrics.network[].tx_bytes` | `tx_bytes` | BigIntegerField | ✅ |
| 31 | Network RX delta | Calculated in serializer | `rx_bytes_delta` | BigIntegerField | ✅ |
| 32 | Network TX delta | Calculated in serializer | `tx_bytes_delta` | BigIntegerField | ✅ |
| 33 | Network RX errors | `metrics.network[].rx_errors` | `rx_errors` | IntegerField | ✅ |
| 34 | Network TX errors | `metrics.network[].tx_errors` | `tx_errors` | IntegerField | ✅ |

### DockerContainerMetric (one row per container per heartbeat — for charts)

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 35 | Docker CPU% | `metrics.docker_containers[].cpu_pct` | `cpu_pct` | FloatField | ✅ |
| 36 | Docker mem usage | `metrics.docker_containers[].mem_usage_bytes` | `mem_usage_bytes` | BigIntegerField | ✅ |

### LatestDockerContainer (latest snapshot per container — for Live Metrics)

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 37 | Docker name | `metrics.docker_containers[].name` | `name` | CharField | — |
| 38 | Docker container ID | `metrics.docker_containers[].container_id` | `container_id` | CharField | — |
| 39 | Docker image | `metrics.docker_containers[].image` | `image` | CharField | — |
| 40 | Docker status | `metrics.docker_containers[].status` | `status` | CharField | — |
| 41 | Docker uptime | `metrics.docker_containers[].uptime_s` | `uptime_s` | IntegerField | — |
| 42 | Docker restarts | `metrics.docker_containers[].restart_count` | `restart_count` | IntegerField | — |
| 43 | Docker mem limit | `metrics.docker_containers[].mem_limit_bytes` | `mem_limit_bytes` | BigIntegerField | — |

### Error Handling

Errors are filtered on the server side — "no error" placeholders from agents
(e.g. `{"source": "kernel", "message": "-- No entries --"}`) are excluded.

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 41 | Error count (real errors only) | `errors[]` (filtered) | `MetricSnapshot.error_count` | IntegerField | ✅ |
| 42 | Latest error text | `errors[]` (filtered) | `Rig.latest_errors_json` | JSONField | ✅ (text) |

### RigStatusEvent (one row per status transition)

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 44 | Rig status transition | `rig.status` change | `status` + `previous_status` | CharField | ✅ |

### GPUProcessMetric (one row per GPU process per heartbeat)

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 45 | GPU process name | `metrics.gpu_processes[].name` | `process_name` | CharField | ✅ (text) |
| 46 | GPU process type | `metrics.gpu_processes[].type` | `type` | CharField | ✅ (text) |
| 47 | GPU process mem | `metrics.gpu_processes[].gpu_mem_mb` | `gpu_mem_mb` | IntegerField | ✅ |

---

## Data Storage Design Decisions

### Dedicated fields vs JSON

| Data | Stored In | Reason |
|------|-----------|--------|
| CPU/memory metrics | Dedicated fields | Queried for charts, need indexing |
| Motherboard info | `motherboard_json` (JSON) | Static data, varies across rigs |
| Software info | `software_json` (JSON) | Static-ish data, varies across rigs |
| CPU load avg | `cpu_load_avg_json` (JSONField[3]) | Small fixed-size array |
| Error count | `error_count` (IntegerField) | Per-snapshot count for error frequency charts |
| Latest error text | `Rig.latest_errors_json` (JSON) | Latest payload only, like motherboard_json |

### Denormalized cache (intentional)

`LatestSnapshot` contains a subset of `MetricSnapshot` fields for fast dashboard loading:
- `cpu_utilization_pct`, `cpu_temp_c`, `mem_used_bytes`, `mem_total_bytes`
- Updated on every heartbeat via serializer
- Read-only cache, not a separate data source

### Error tracking evolution

- **Before:** `ErrorEventOccurrence` table stored per-occurrence timestamps (99K rows, 49% of all data)
- **After (current):**
  - `MetricSnapshot.error_count` (int) — single integer per snapshot for error frequency charts
  - `Rig.latest_errors_json` (JSON) — latest error text from most recent payload (like motherboard_json, updated in place)
  - Error frequency chart uses `SUM(error_count)` grouped by time bucket on MetricSnapshot
  - "Latest Errors" tab reads from `Rig.latest_errors_json`
  - No per-snapshot error text storage — only the latest payload's errors are kept

---

## Historical Issues Found and Resolved

### Issue 1: Live Metrics showing stale data
**Problem:** Test payloads with fake timestamps polluted the database.
**Fix:** Cleaned up test data. Live Metrics now uses `DISTINCT ON` for latest-per-device queries.

### Issue 2: Test data pollution
**Problem:** Multiple test payloads inserted during development.
**Fix:** Deleted all test data. Keep only data from actual agent heartbeats.

### Issue 3: `uptime_s` stored in both MetricSnapshot and software_json
**Problem:** Redundant storage.
**Fix:** Removed dedicated `uptime_s` field. Now reads from `software_json.uptime_s`.

### Issue 4: `status` field on MetricSnapshot vs Rig.status
**Problem:** Potential conflict between per-heartbeat status and current status.
**Fix:** Intentional. `MetricSnapshot.status` records status AT heartbeat time. `Rig.status` is current status.

### Issue 5: ErrorEventOccurrence table bloat + ErrorEvent dedup table
**Problem:** 99K rows (49% of all data) in ErrorEventOccurrence + separate ErrorEvent dedup table. Error text stored per-snapshot in error_json was also wasteful.
**Fix:** Replaced with single `MetricSnapshot.error_count` (integer) for charts + `Rig.latest_errors_json` (latest error text only, updated in place). Dropped both ErrorEventOccurrence and ErrorEvent tables entirely. ~50% storage reduction.

### Issue 6: Chart data truncation
**Problem:** `[:10000]` and `[:50000]` queryset limits truncated 7d/30d chart data.
**Fix:** Removed all limits. SQL-level aggregation returns exact data points needed.

---

## Code References

### Ingest pipeline
- `metrics_app/views.py` — `IngestView.post()` → `process_ingest()`
- `metrics_app/serializers.py` — `IngestSerializer`, `process_ingest()`
- Transaction: `transaction.atomic()` wraps all DB operations

### Chart queries
- `metrics_app/views.py` — `ChartDataView.get()`
- SQL aggregation: `annotate(TruncHour('timestamp')).values().annotate(Avg/Sum(...))`
- No queryset LIMITS — returns exact data points

### Live metrics
- `dashboard/views.py` — `_fetch_rig_metrics()` uses `DISTINCT ON`
- `dashboard/views.py` — `rig_detail()` passes metrics to template

### Data retention
- `metrics_app/management/commands/compact_data.py` — single phase, 1-hour buckets
- `metrics_app/management/commands/cleanup_old_data.py` — batch deletion
- `metrics_app/management/commands/backfill_historical_data.py` — test data generation
