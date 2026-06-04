# Time Series Data Expansion — Analysis & Plan

## ✅ Phase 1 — IMPLEMENTED

### Changes made:
1. **MetricSnapshot** — added `uptime_s` (BigIntegerField) and `status` (CharField)
2. **NetworkMetric** — added `rx_bytes_delta` and `tx_bytes_delta` (BigIntegerField)
3. **Serializer** — stores `uptime_s` from payload, `status` from Rig model
4. **Serializer** — calculates network deltas by comparing consecutive readings per interface
5. **Views** — passes `rig` object to `process_ingest` for status tracking
6. **Migration 0007** — applies all new fields

### Verification:
- First network reading: delta=None (no previous reading)
- Second reading: delta correctly calculated (rx_delta=10MB, tx_delta=5MB)
- Uptime and status stored correctly per heartbeat

## Goal
Identify all data from the agent payload that changes over time and should be stored
in the database as time-series data. This enables historical charts for every metric.

## Current State — What's Already Stored

### MetricSnapshot (per-minute, per-rig)
- CPU: model, util%, temp, cores, load_avg
- Memory: total, used, free, cached, swap_used, swap_total
- Motherboard: JSON (static)
- Software: JSON (static, but uptime_s changes)

### GPUMetric (per-minute, per-GPU)
- uuid, model, util%, temp, fan%, mem_total/used/free, mem_util%, power_draw/limit

### StorageMetric (per-minute, per-device)
- device, mountpoint, fstype, capacity, usage%, temp, smart_health

### NetworkMetric (per-minute, per-interface)
- interface, ipv4, link_speed, rx_bytes, tx_bytes, rx_errors, tx_errors

### DockerContainerMetric (per-minute, per-container)
- name, image, status, restart_count

### ErrorEvent (deduplicated)
- source, message, hash, count, last_seen

### LatestSnapshot (denormalized latest)
- cpu_util%, cpu_temp, mem_used, mem_total

## Missing Data — What Should Be Added

### 1. Uptime tracking (NEW field on MetricSnapshot)
**Why:** Track when rig was restarted. uptime_s resets on reboot.
**Data:** `uptime_s` from software_json (already sent by agent)
**Change:** Add `uptime_s = models.BigIntegerField(null=True)` to MetricSnapshot
**Chart:** Line chart showing uptime over time, with resets visible as drops

### 2. Network traffic delta (NEW model: NetworkTraffic)
**Why:** rx_bytes/tx_bytes are cumulative counters. To plot traffic rate (bytes/s),
we need to calculate deltas between consecutive measurements.
**Data:** Store rx_bytes and tx_bytes per interface per minute, plus calculated rates
**Change:** Add `rx_bytes_delta` and `tx_bytes_delta` fields to NetworkMetric
**Chart:** Line chart showing network throughput (MB/s) per interface over time

### 3. Docker container details (ENHANCE DockerContainerMetric)
**Why:** Container status changes over time. Currently stored but could be enhanced.
**Data:** Already stored: name, image, status, restart_count
**Enhancement:** Add `cpu_pct`, `mem_usage_bytes`, `net_rx_bytes`, `net_tx_bytes` if available
**Note:** Docker SDK provides container.stats() for this. Future enhancement.

### 4. AI processes (NEW model: AIProcessMetric)
**Why:** Agent collects `ai_processes` array (currently empty placeholder). When implemented,
this will track GPU-using processes over time.
**Data:** process_name, pid, gpu_uuid, gpu_mem_used_mb, cpu_pct
**Change:** Create new model `AIProcessMetric` with FK to MetricSnapshot
**Chart:** Stacked bar chart showing GPU memory usage by process over time

### 5. Rig status history (NEW model: RigStatusEvent)
**Why:** Track when rig goes online/offline/stale. Useful for uptime reporting.
**Data:** status (online/offline/stale), timestamp
**Change:** Create new model `RigStatusEvent`
**Change:** Also add `status` field to MetricSnapshot to track per-heartbeat status
**Chart:** Timeline showing rig availability, downtime periods

### 6. Error events time series (ENHANCE ErrorEvent)
**Why:** Currently errors are deduplicated with count. For charts, we need per-occurrence timestamps.
**Data:** Each error occurrence with its timestamp
**Change:** Add `ErrorEventOccurrence` model with FK to ErrorEvent + timestamp
**Chart:** Error frequency over time, error type distribution

## Recommended Implementation Priority

### Phase 1: Quick wins (no new models)
1. Add `uptime_s` to MetricSnapshot — already in payload, just need to store it
2. Add `status` to MetricSnapshot — track rig status per heartbeat
3. Add `rx_bytes_delta`/`tx_bytes_delta` to NetworkMetric — calculate from consecutive readings

### Phase 2: New models
4. Create `RigStatusEvent` model — track status transitions
5. Create `AIProcessMetric` model — prepare for future AI process tracking
6. Create `ErrorEventOccurrence` model — per-occurrence error tracking

### Phase 3: Enhanced collectors
7. Enhance Docker container collection with CPU/memory stats
8. Implement AI process collection (nvidia-smi + psutil)

## Database Changes Summary

### MetricSnapshot — add fields:
- `uptime_s = models.BigIntegerField(null=True)` — track uptime over time
- `status = models.CharField(max_length=10, null=True)` — rig status at this snapshot

### NetworkMetric — add fields:
- `rx_bytes_delta = models.BigIntegerField(null=True)` — bytes received since last reading
- `tx_bytes_delta = models.BigIntegerField(null=True)` — bytes sent since last reading

### New model: RigStatusEvent
- `rig_uuid` — FK to rig
- `timestamp` — when status changed
- `status` — online/offline/stale
- `previous_status` — what it was before

### New model: AIProcessMetric
- `snapshot` — FK to MetricSnapshot
- `rig_uuid`, `timestamp`
- `process_name`, `pid`
- `gpu_uuid` — which GPU this process uses
- `gpu_mem_used_mb` — GPU memory used by this process
- `cpu_pct` — CPU usage

### New model: ErrorEventOccurrence
- `error_event` — FK to ErrorEvent
- `timestamp` — when this occurrence happened

---

## ✅ Phase 4 — Deduplication Cleanup (IMPLEMENTED)

### Removed redundant field:
- **`MetricSnapshot.uptime_s`** — was redundant with `software_json.uptime_s`
- The `software_json` field already contained `uptime_s` from the agent payload
- Now reads uptime from `metric_snapshot.software_json.uptime_s` in templates
- Migration 0010

### Intentional redundancy kept:
- **`LatestSnapshot`** — denormalized cache for fast dashboard loading
  - Avoids JOIN with latest MetricSnapshot on every page load
  - Contains: cpu_utilization_pct, cpu_temp_c, mem_used_bytes, mem_total_bytes
  - Updated on every heartbeat via serializer

### Final data storage (no unnecessary duplication):
- **MetricSnapshot:** dedicated fields for CPU/memory/status + JSON for motherboard/software
- **software_json** contains: hostname, os_distro, kernel, uptime_s, nvidia_driver, docker_version
- **uptime_s** is NOT stored as a separate field — read from software_json
