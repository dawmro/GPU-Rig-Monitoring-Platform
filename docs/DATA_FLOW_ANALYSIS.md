# Data Flow Analysis — Complete Reference

## Principle
"Main source of data for numeric values that change over time should always be a dedicated database field that stores time series values. We should always save payload data there and read from there for both Live Metrics and charts."

**Status:** ✅ Principle followed. Every numeric value that changes over time is stored in a dedicated database field and read from that field for display and charts.

---

## Complete Data Model — Payload to DB Field Mapping

### MetricSnapshot (one row per rig per heartbeat — chart data only)

Stores dynamic metrics for historical chart aggregation. Static fields live in LatestSnapshot.

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
| 10 | Uptime | `software.uptime_s` | `uptime_s` | PositiveIntegerField | ✅ |
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
| 29 | Storage capacity | `metrics.storage[].capacity_bytes` | `capacity_bytes` | BigIntegerField | — |
| 30 | SMART health | `metrics.storage[].smart_health` | `smart_health` | CharField | — |
| 31 | Read bytes (cumulative) | `metrics.storage[].read_bytes` | `read_bytes` | BigIntegerField | — |
| 32 | Write bytes (cumulative) | `metrics.storage[].write_bytes` | `write_bytes` | BigIntegerField | — |
| 33 | Read bytes (delta) | Computed in serializer | `read_bytes_delta` | BigIntegerField | ✅ |
| 34 | Write bytes (delta) | Computed in serializer | `write_bytes_delta` | BigIntegerField | ✅ |
| 35 | Read IOPS (cumulative) | `metrics.storage[].read_iops` | `read_iops` | PositiveIntegerField | — |
| 36 | Write IOPS (cumulative) | `metrics.storage[].write_iops` | `write_iops` | PositiveIntegerField | — |
| 37 | Read IOPS (delta) | Computed in serializer | `read_iops_delta` | PositiveIntegerField | ✅ |
| 38 | Write IOPS (delta) | Computed in serializer | `write_iops_delta` | PositiveIntegerField | ✅ |
| 39 | Busy time (cumulative) | `metrics.storage[].busy_time_ms` | `busy_time_ms` | PositiveIntegerField | — |
| 40 | Utilization % | Computed in serializer | `utilization_pct` | FloatField | ✅ |

### NetworkMetric (one row per interface per heartbeat)

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 29 | Network RX bytes | `metrics.network[].rx_bytes` | `rx_bytes` | BigIntegerField | ✅ |
| 30 | Network TX bytes | `metrics.network[].tx_bytes` | `tx_bytes` | BigIntegerField | ✅ |
| 31 | Network RX delta | Calculated in serializer | `rx_bytes_delta` | BigIntegerField | ✅ |
| 32 | Network TX delta | Calculated in serializer | `tx_bytes_delta` | BigIntegerField | ✅ |
| 33 | Network RX errors | `metrics.network[].rx_errors` | `rx_errors` | IntegerField | ✅ |
| 34 | Network TX errors | `metrics.network[].tx_errors` | `tx_errors` | IntegerField | ✅ |

### DockerContainerMetric — REMOVED

This model has been removed. Per-container CPU/memory usage is no longer collected.
Docker container data is now stored only as a latest snapshot (see LatestDockerContainer).

### LatestDockerContainer (latest snapshot per container — for Live Metrics)

| # | Value | Payload Path | DB Field | Type | Charts? |
|---|-------|-------------|----------|------|---------|
| 37 | Docker name | `metrics.docker_containers[].name` | `name` | CharField | — |
| 38 | Docker container ID | `metrics.docker_containers[].container_id` | `container_id` | CharField | — |
| 39 | Docker image | `metrics.docker_containers[].image` | `image` | CharField | — |
|| 40 | Docker status | `metrics.docker_containers[].status` | `status` | CharField | — |
|| 41 | Docker created | `metrics.docker_containers[].created` | `created` | CharField | — |
|| 42 | Docker status text | `metrics.docker_containers[].status_text` | `status_text` | CharField | — |

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
| CPU/memory metrics | MetricSnapshot (dedicated fields) | Queried for charts, need indexing |
| CPU model, cores | LatestSnapshot | Static per rig, overwritten on heartbeat |
| Motherboard info | LatestSnapshot (`motherboard_json`) | Static per rig, overwritten on heartbeat |
| Software info | LatestSnapshot (`software_json`) | Static per rig, overwritten on heartbeat |
| CPU load avg | MetricSnapshot (`cpu_load_avg_json`) | Small fixed-size array, charted |
| Error count | MetricSnapshot (`error_count`) | Per-snapshot count for error frequency charts |
| Latest error text | `Rig.latest_errors_json` (JSON) | Latest payload only |

### Denormalized cache (LatestSnapshot)

`LatestSnapshot` is a single row per rig, updated on every heartbeat. It stores ALL data needed for dashboard display (Fleet Overview + Live Metrics), eliminating timeseries queries entirely for display views.

**Design principle:** During ingest, the serializer writes to both timeseries tables (for charts) and LatestSnapshot (for display). During display reads, only LatestSnapshot is queried. Charts still read from timeseries tables.

**Fields stored in LatestSnapshot:**

| Category | Fields | Count |
|---|---|---|
| CPU | schema_version, timestamp, cpu_model, cpu_physical_cores, cpu_logical_cores, cpu_utilization_pct, cpu_temp_c, cpu_load_avg_json | 8 |
| Memory | mem_total_bytes, mem_used_bytes, mem_free_bytes, mem_cached_bytes, swap_total_bytes, swap_used_bytes | 6 |
| System | uptime_s, motherboard_json, software_json, agent_version | 4 |
| GPU (×N) | gpu_count, gpu_models_json, gpu_temps_json, gpu_utils_json, gpu_fans_json, gpu_core_clocks_json, gpu_mem_clocks_json, gpu_mem_used_json, gpu_mem_total_json, gpu_mem_util_pcts_json, gpu_mem_free_json, gpu_power_draws_json, gpu_power_limits_json, gpu_pcie_gen_json, gpu_pcie_max_gen_json, gpu_pcie_width_json, gpu_pcie_max_width_json | 17 |
|| Storage (×N) | storage_count, storage_devices_json, storage_fstypes_json, storage_mountpoints_json, storage_capacities_json, storage_usage_pcts_json, storage_temps_json, storage_smart_json, storage_read_bytes_delta_json, storage_write_bytes_delta_json, storage_read_iops_delta_json, storage_write_iops_delta_json, storage_utilization_pcts_json, storage_read_bytes_total_json, storage_write_bytes_total_json, storage_read_iops_total_json, storage_write_iops_total_json | 17 |
|| Network (×N) | network_count, network_interfaces_json, network_ipv4s_json, network_speeds_json, network_rx_bytes_json, network_tx_bytes_json, network_rx_errors_json, network_tx_errors_json | 8 |
|| Metadata | updated_at (auto) | 1 |
|| **Total** | | **~61 fields** |

**Views using LatestSnapshot:**
- `rig_list` (Fleet Overview): Reads LatestSnapshot + Rig + RigTag. **0 timeseries queries.**
- `htmx_metrics` (Live Metrics): Reads LatestSnapshot + LatestDockerContainer + GPUProcessMetric. **0 timeseries queries for GPU/storage/network.**

**Views still using timeseries:**
- `ChartDataView` (Historical Charts): Reads GPUMetric, StorageMetric, NetworkMetric, MetricSnapshot for time-series aggregation. **Unchanged.**

**Separation summary:**
- Snapshot → Display (Fleet Overview, Live Metrics) → 0 timeseries queries
- Timeseries → Charts (ChartDataView) → All timeseries queries
- The two paths are completely independent after ingest.

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
**Fix:** Cleaned up test data. Live Metrics now reads from LatestSnapshot (single row lookup, no DISTINCT ON).

### Issue 5: Fleet Overview and Live Metrics timeseries bottleneck
**Problem:** Fleet Overview queried GPUMetric/StorageMetric/NetworkMetric with DISTINCT ON for every rig. Live Metrics queried 3 timeseries tables per poll. With 100+ rigs, this caused 2000+ queries and 20+ second load times.
**Fix:** All display data (GPU, storage, network) moved to LatestSnapshot JSON arrays during ingest. Fleet Overview and Live Metrics now execute 0 timeseries queries. Historical Charts still use timeseries tables (unchanged).

### Issue 2: Test data pollution
**Problem:** Multiple test payloads inserted during development.
**Fix:** Deleted all test data. Keep only data from actual agent heartbeats.

### Issue 3: `uptime_s` stored in both MetricSnapshot and software_json
**Problem:** Redundant storage.
**Fix:** Removed dedicated `uptime_s` field. Now reads from `software_json.uptime_s`.

### Issue 7: `uptime_s` removed from MetricSnapshot during static field cleanup
**Problem:** When static fields (cpu_model, motherboard_json, software_json, etc.) were moved from MetricSnapshot to LatestSnapshot, `uptime_s` was incorrectly removed from MetricSnapshot along with `software_json`. The uptime chart queries MetricSnapshot and broke.
**Fix:** Added dedicated `uptime_s = PositiveIntegerField(null=True)` to MetricSnapshot. Uptime is a dynamic value (increases over time) that must stay in the timeseries table for chart aggregation. Other static fields (cpu_model, motherboard_json, software_json, agent_version) remain in LatestSnapshot only.

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
- `dashboard/views.py` — `_fetch_rig_metrics()` reads from LatestSnapshot JSON arrays (no timeseries queries for GPU/storage/network)
- `dashboard/views.py` — `rig_detail()` passes metrics to template

### Data retention
- `metrics_app/management/commands/compact_data.py` — single phase, 1-hour buckets
- `metrics_app/management/commands/cleanup_old_data.py` — batch deletion
- `metrics_app/management/commands/backfill_historical_data.py` — test data generation
