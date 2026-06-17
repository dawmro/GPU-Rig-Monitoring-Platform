# Documentation Update Plan

## Summary of Code Changes Since Last Doc Update

### New Features
1. **Disk I/O monitoring** — read_bytes, write_bytes, read_iops, write_iops, busy_time_ms, utilization_pct (10 new fields in StorageMetric)
2. **Cumulative totals in LatestSnapshot** — 4 new JSON arrays for Total Read/Write/IOPS display
3. **format_bytes_total template filter** — human-readable sizes (KB/MB/GB/TB)
4. **Windows wmic CLI drive mapping** — reliable partition-to-physical-disk mapping
5. **Docker crash fix** — graceful handling when docker binary not found
6. **Schema version 1.7** — new disk I/O fields in agent payload

### Files That Need Updates

---

## 1. GPU_Rig_Monitoring_Architecture.md (65KB, 1336 lines)

### §3.3 Payload Schema (v1.1 → v1.7)
**Current:** Shows schema 1.6 with old docker_containers fields
**Needs:** Update to schema 1.7 with new disk I/O fields

Storage payload example needs:
```json
"read_bytes": 37688539648,
"write_bytes": 156538570752,
"read_iops": 3309393,
"write_iops": 6397960,
"busy_time_ms": null
```

### §3.5 Two Agents
**Current:** Linux 1.5.7, Windows 1.6.7-win, schema 1.6
**Needs:** Linux 1.5.9, Windows 1.6.10-win, schema 1.7

### §5.4 Tab Layout — Live Metrics
**Current:** "Docker container count, storage disks, recent errors"
**Needs:** "Storage card shows: device, capacity, usage%, temp, SMART, Total Read/Write (cumulative), Since last update (delta), IOPS, Utilization%"

### §5.5 Snapshot-Timeseries Decoupling
**Current:** Storage fields: 7 JSON arrays
**Needs:** Storage fields: 11 JSON arrays (added read/write/iops delta + total)

### §6.1 Table Summary — StorageMetric
**Current:** "Per-disk metrics (capacity, usage%, temp, SMART health)"
**Needs:** "Per-disk metrics (capacity, usage%, temp, SMART health, read/write bytes, read/write IOPS, busy_time_ms, utilization%)"

### §6.1 Table Summary — LatestSnapshot
**Current:** "7 storage JSON arrays"
**Needs:** "11 storage JSON arrays (devices, fstypes, mountpoints, capacities, usage%, temps, smart, read/write/iops deltas, read/write/iops totals)"

### §6.1b Management Commands
**Current:** compact_data description doesn't mention disk I/O fields
**Needs:** "Aggregates disk I/O fields: SUM for deltas, AVG for utilization, LAST for cumulative counters"

### §10.2 Write Throughput
**Current:** Doesn't mention disk I/O writes
**Needs:** Add disk I/O to ingest pipeline table

---

## 2. DATA_FLOW_ANALYSIS.md (14KB, 227 lines)

### Storage Section (lines 43-70)
**Current:** Only shows capacity_bytes, usage_pct, temp_c, smart_health
**Needs:** Add all 10 new fields with payload paths and types

### LatestSnapshot Section (lines 130-146)
**Current:** Storage: 8 fields
**Needs:** Storage: 12 fields (added read/write/iops delta + total JSON arrays)

### Denormalized Cache Section
**Current:** "Storage (×N) | 7 JSON arrays"
**Needs:** "Storage (×N) | 11 JSON arrays"

---

## 3. DATA_RETENTION_PLAN.md (8KB, 242 lines)

### Compaction Section
**Current:** Only mentions usage_pct, temp_c, capacity_bytes aggregation
**Needs:** Add disk I/O aggregation: "SUM for deltas (read_bytes_delta, write_bytes_delta, read_iops_delta, write_iops_delta), AVG for utilization_pct, LAST for cumulative counters (read_bytes, write_bytes, read_iops, write_iops, busy_time_ms)"

---

## 4. DOCUMENTATION_AUDIT.md (3KB, 66 lines)

### Agent Versions
**Current:** "Linux 1.5.7, Windows 1.6.7-win, schema 1.6"
**Needs:** "Linux 1.5.9, Windows 1.6.10-win, schema 1.7"

---

## 5. BACKFILL_ANALYSIS.md (7KB)

### Disk Table Schema
**Current:** Only shows capacity_bytes, usage_pct, temp_c, smart_health
**Needs:** Add all 10 new fields

---

## 6. INGEST_PERFORMANCE_ANALYSIS.md (6KB)

### Disk I/O Impact
**Current:** No mention of disk I/O fields
**Needs:** "Each ingest now stores 10 additional disk I/O fields per disk (read_bytes, write_bytes, read_bytes_delta, write_bytes_delta, read_iops, write_iops, read_iops_delta, write_iops_delta, busy_time_ms, utilization_pct). Measured impact: ~5% increase in ingest time for typical 5-disk system."

---

## 7. CHART_PERFORMANCE_ANALYSIS.md (2KB)

### New Chart Metrics
**Current:** Only mentions disk_usage_pct
**Needs:** "New chart metrics: disk_read_bytes_delta, disk_write_bytes_delta, disk_read_iops_delta, disk_write_iops_delta, disk_utilization_pct. All use SQL-level aggregation (SUM for deltas, AVG for utilization)."

---

## Priority Order

1. **GPU_Rig_Monitoring_Architecture.md** — Main reference doc, most impact
2. **DATA_FLOW_ANALYSIS.md** — Complete field mapping reference
3. **DATA_RETENTION_PLAN.md** — Compaction strategy
4. **DOCUMENTATION_AUDIT.md** — Version numbers
5. **BACKFILL_ANALYSIS.md** — Schema reference
6. **INGEST_PERFORMANCE_ANALYSIS.md** — Performance impact
7. **CHART_PERFORMANCE_ANALYSIS.md** — Chart metrics
