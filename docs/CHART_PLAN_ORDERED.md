# Chart Implementation Plan — Ordered by Category

## Current Charts (7 existing)

| # | Chart | Metric | Source | Status |
|---|-------|--------|--------|--------|
| 1 | GPU Temperature | `gpu_temp_c` | GPUMetric (gpu_index=0) | ✅ Works |
| 2 | GPU Utilization | `gpu_util_pct` | GPUMetric (gpu_index=0) | ✅ Works |
| 3 | GPU Memory | `gpu_mem_used_mb` | GPUMetric (gpu_index=0) | ✅ Works |
| 4 | GPU Power | `gpu_power_w` | GPUMetric (gpu_index=0) | ✅ Works |
| 5 | CPU Utilization | `cpu_utilization_pct` | MetricSnapshot | ✅ Works |
| 6 | CPU Temperature | `cpu_temp_c` | MetricSnapshot | ✅ Works |
| 7 | Memory Usage | `mem_used_bytes` | MetricSnapshot | ✅ Fixed |

## New Charts Added This Session (3 new)

| # | Chart | Metric | Source | Status |
|---|-------|--------|--------|--------|
| 8 | Memory Free | `mem_free_bytes` | MetricSnapshot | ✅ New |
| 9 | Swap Usage | `swap_used_bytes` | MetricSnapshot | ✅ New |
| 10 | CPU Load Average | `cpu_load_avg_json` | MetricSnapshot | ✅ New (3-line) |

## Planned Order (as requested)

### Phase 1: CPU ✅ COMPLETE
- CPU Utilization ✅
- CPU Temperature ✅
- CPU Load Average ✅ (3-line: 1m/5m/15m)

### Phase 2: GPU — NEEDS REWORK for multi-GPU
See multi-GPU analysis below.

### Phase 3: Memory & Swap ✅ COMPLETE
- Memory Usage ✅
- Memory Free ✅
- Swap Usage ✅

### Phase 4: Storage (planned)
- Disk Usage % (all disks, not just first)
- Disk Temperature (all disks)

### Phase 5: Network (planned)
- Network RX Rate (per interface)
- Network TX Rate (per interface)
- Network Errors (per interface)

### Phase 6: Docker, AI Processes, Rig Health (planned)
- Container CPU %, Memory, Restarts
- AI Process GPU Memory
- Uptime, Error Frequency

---

## Multi-GPU Chart Approaches — Analysis

### Current Behavior
GPU charts use `gpu_index=0` hardcoded. Only the first GPU is shown.

### Approach A: Separate Charts per GPU (one canvas per GPU per metric)

**Example:** 4 GPUs × 4 metrics = 16 separate charts

**Pros:**
- Simple implementation — just loop over gpu_index in template
- Each chart is independent, clear labeling ("GPU 0 Temp", "GPU 1 Temp", etc.)
- Easy to show/hide individual GPUs
- Works with existing `loadChart()` function unchanged
- No changes to ChartDataView needed

**Cons:**
- Chart explosion — 4 GPUs × 4 metrics = 16 charts vs. 4 charts
- Hard to compare GPUs at a glance (need to scroll between charts)
- Takes up a lot of vertical space
- For rigs with 8+ GPUs, becomes unwieldy

**Implementation difficulty:** LOW
- Template: loop `{% for gpu in gpus %}` creating canvas + loadChart per GPU
- Backend: no changes needed (already supports `gpu_index` query param)

---

### Approach B: Multi-Series Single Chart (one canvas per metric, one dataset per GPU)

**Example:** 4 metrics × 1 chart each = 4 charts, each with 4 colored lines (one per GPU)

**Pros:**
- Compact — same number of charts regardless of GPU count
- Easy to compare GPUs at a glance (lines on same axes)
- Consistent with how CPU Load Average already works (3 lines, 1 chart)
- Scales well — 2 GPUs or 8 GPUs, same number of charts
- Legend identifies each GPU by UUID or index

**Cons:**
- Need to extend ChartDataView to return multi-GPU data in one query
- Need to extend `loadChart()` or create `loadChartMultiGpu()` for multi-dataset rendering
- With many GPUs (8+), legend becomes crowded
- Colors need to be distinct for each GPU
- GPU UUIDs in legend are long — need truncation or friendly naming

**Implementation difficulty:** MEDIUM
- Backend: ChartDataView needs new query path — filter by `rig_uuid` only (no `gpu_index`), group by `gpu_uuid`, return one dataset per GPU
- Frontend: `loadChart()` already supports multi-dataset (load avg proves this) — but needs GPU-specific colors and labels
- Need to fetch GPU list first (to know how many datasets to expect)

---

### Approach C: Hybrid — Multi-series for related metrics, separate for unrelated

**Example:**
- GPU Temperature: 1 chart, all GPUs (comparing thermal behavior)
- GPU Utilization: 1 chart, all GPUs (comparing workload)
- GPU Memory: 1 chart, all GPUs (comparing VRAM usage)
- GPU Power: 1 chart, all GPUs (comparing power draw)
- GPU Fan Speed: 1 chart, all GPUs (comparing cooling)

**Pros:**
- Best of both worlds — compact yet clear
- Each chart shows one metric across all GPUs
- Easy to spot outliers (one GPU hotter than others)
- 5 charts total regardless of GPU count

**Cons:**
- Same implementation complexity as Approach B
- Need GPU identification (UUID truncation or "GPU 0: RTX 3060" labels)

**Implementation difficulty:** MEDIUM (same as B)

---

## Recommendation

**Approach C (Hybrid Multi-Series)** is the best balance:
- Same chart count regardless of GPU count
- Direct GPU-to-GPU comparison on same axes
- Consistent with existing CPU Load Average pattern
- Scales from 1 to 8+ GPUs gracefully

### Implementation Plan for Approach C

**Backend changes:**
1. Add new query parameter `multi_gpu=true` to ChartDataView
2. When `multi_gpu=true`, query ALL GPUs for the rig (no `gpu_index` filter)
3. Group results by `gpu_uuid`, create one dataset per GPU
4. Label datasets as "GPU 0: RTX 3060", "GPU 1: RTX 4090", etc. (truncated)

**Frontend changes:**
1. Replace per-GPU `loadChart()` calls with `loadChartMultiGpu()` calls
2. `loadChartMultiGpu()` fetches with `multi_gpu=true`, renders multi-dataset chart
3. Use distinct colors per GPU (predefined palette for up to 8 GPUs)
4. Legend shows GPU model name for identification

**GPU identification:**
- Query `GPUMetric` for distinct `gpu_uuid` + `model` combinations
- Label format: "GPU 0: RTX 3060" or just "RTX 3060" if only one GPU of that model
- Fallback: "GPU 0", "GPU 1" if model unknown
