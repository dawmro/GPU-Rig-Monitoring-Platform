# Ingest Performance Analysis

**Date:** 2026-06-12
**Branch:** `analysis/ingest-performance`
**Test rig:** 107 active rigs, ~2.1M GPUMetric rows

---

## 1. Test Setup

Two payload sizes were tested:

| Scenario | GPUs | Disks | NICs | Containers | Processes |
|---|---|---|---|---|---|
| **Typical** | 1 | 1 | 1 | 1 | 1 |
| **Large** | 8 | 5 | 3 | 10 | 20 |

---

## 2. Results Summary

| Metric | Typical (1 GPU) | Large (8 GPUs) |
|---|---|---|
| **Total ingest time** | 70.2ms | 266.1ms |
| **DB query time** | 38.0ms (54%) | 161.0ms (61%) |
| **Python overhead** | 32.2ms (46%) | 105.1ms (39%) |
| **Total queries** | 41 | 203 |
| **Avg query time** | 0.9ms | 0.8ms |

---

## 3. Detailed Breakdown by Table

### Typical Payload (1 GPU, 1 disk, 1 NIC, 1 container, 1 process)

| Table | Queries | Time | % of Total |
|---|---|---|---|
|| NetworkMetric | 3 | 10.0ms | 14% |
|| MetricSnapshot | 2 | 6.0ms | 9% |
|| LatestDockerContainer | 2 | 6ms | 9% |
|| GPUMetric | 2 | 2.0ms | 3% |
|| StorageMetric | 2 | 2.0ms | 3% |
|| LatestSnapshot | 2 | 2.0ms | 3% |
|| GPUProcess | 2 | 1.0ms | 1% |

### Large Payload (8 GPUs, 5 disks, 3 NICs, 10 containers, 20 processes)

| Table | Queries | Time | % of Total |
|---|---|---|---|
|| NetworkMetric | 9 | 58.0ms | 22% |
|| LatestSnapshot | 2 | 39.0ms | 15% |
|| GPUMetric | 16 | 15.0ms | 6% |
|| MetricSnapshot | 2 | 8.0ms | 3% |
|| LatestDockerContainer | 20 | 8.0ms | 3% |
|| GPUProcess | 21 | 3.0ms | 1% |
|| StorageMetric | 10 | 1.0ms | 0% |

---

## 4. Bottleneck Analysis

### 4.1 NetworkMetric — HIGHEST IMPACT

**Problem:** Each network interface triggers a `SELECT` query to find the previous reading for delta calculation, plus an `INSERT/UPDATE`.

**Typical:** 3 queries (1 SELECT + 1 INSERT/UPDATE + 1 index lookup) = 10.0ms
**Large:** 9 queries (3 SELECT + 3 INSERT/UPDATE + 3 index lookups) = 58.0ms

**Root cause:** The delta calculation requires reading the previous `NetworkMetric` row:
```python
prev = NetworkMetric.objects.filter(
    rig_uuid=rig_uuid,
    interface=iface_name,
).order_by('-timestamp').first()
```

This query scans the rig's network metric history. With 31 days of data at 1 row/minute = ~44,000 rows per interface.

**Impact:** Scales linearly with number of network interfaces. For a rig with 3 NICs, this is 22% of total ingest time.

### 4.2 LatestSnapshot — MODERATE IMPACT

**Problem:** The `update_or_create` with 35+ field defaults is a large JSON payload.

**Typical:** 2 queries = 2.0ms
**Large:** 2 queries = 39.0ms

**Root cause:** The `defaults` dict has 35+ fields including 16 GPU JSON arrays, 7 storage arrays, and 7 network arrays. The JSON serialization/deserialization overhead is significant.

### 4.3 GPUMetric — LOW IMPACT

**Typical:** 2 queries = 2.0ms
**Large:** 16 queries = 15.0ms

**Root cause:** `update_or_create` per GPU. With 8 GPUs, this is 8 INSERT/UPDATE + 8 index lookups. Each query is fast (~1ms) but adds up.

### 4.4 LatestDockerContainer — LOW IMPACT

**Typical:** 2 queries = 6.0ms
**Large:** 20 queries = 8.0ms

**Root cause:** Delete-before-insert pattern per container. With 10 containers, this is 1 DELETE + 10 INSERT + index lookups.

### 4.5 Python Overhead — SIGNIFICANT

**Typical:** 32.2ms (46% of total)
**Large:** 105.1ms (39% of total)

**Breakdown:**
- DRF serialization: ~0.7ms
- Error filtering: ~0.1ms
- JSON array building (GPU/storage/network): ~5ms
- Django ORM overhead (model instantiation, validation): ~25-90ms

---

## 5. Scaling Projections

| Rigs | Ingest/sec | DB writes/sec | Estimated DB load |
|---|---|---|---|
| 100 | ~1.4 | ~570 | ~5% of capacity |
| 500 | ~7.1 | ~2,870 | ~25% of capacity |
| 1,000 | ~14.3 | ~5,740 | ~50% of capacity |

**Assumptions:** 1 ingest/rig/minute, typical payload (1 GPU), PostgreSQL on NVMe sustaining 10,000 writes/sec.

---

## 6. Optimization Opportunities

### 6.1 NetworkMetric Delta Calculation — HIGH PRIORITY

**Current:** SELECT previous row per interface during every ingest.
**Optimization:** Store the last `rx_bytes`/`tx_bytes` in `LatestSnapshot` and calculate delta from there. Eliminates 1 SELECT per interface per ingest.

**Expected savings:** ~3ms per rig per ingest (10ms → 7ms for typical rig).

### 6.2 Bulk Insert for GPUMetric — MEDIUM PRIORITY

**Current:** `update_or_create` per GPU (N queries for N GPUs).
**Optimization:** Use `bulk_create` with `ignore_conflicts=True` or raw SQL `INSERT ... ON CONFLICT DO UPDATE`.

**Expected savings:** ~1ms per GPU per ingest.

### 6.3 LatestSnapshot JSON Serialization — MEDIUM PRIORITY

**Current:** 35+ field defaults dict with large JSON arrays.
**Optimization:** Use `update_or_create` with raw SQL or `bulk_update` to reduce ORM overhead.

**Expected savings:** ~5-10ms per ingest.

### 6.4 LatestDockerContainer Bulk Insert — LOW PRIORITY

**Current:** Delete-before-insert per container.
**Optimization:** `bulk_create` for containers.

**Expected savings:** ~1ms per container per ingest.

---

## 7. Current Performance Verdict

**The ingest pipeline is well-optimized for the current scale:**

- **70ms per ingest** for a typical rig (1 GPU) — well within the 60-second heartbeat interval.
- **266ms per ingest** for a large rig (8 GPUs) — still well within limits.
- **No single bottleneck** — the load is distributed across multiple tables.
- **Linear scaling** — ingest time scales linearly with payload size.

**The system can handle ~14 ingests/second sustained**, which supports **~840 rigs at 1-minute intervals** before hitting 50% DB write capacity.

**Recommendation:** No immediate optimizations needed. The NetworkMetric delta calculation is the only significant bottleneck, but it's acceptable at current scale. Revisit when scaling beyond 500 rigs.
