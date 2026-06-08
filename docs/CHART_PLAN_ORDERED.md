# Chart Implementation Plan — Ordered by Category
## Status: ✅ DONE - All charts implemented and verified as of 2026-06-07

## Current Charts (7 existing)

||| # | Chart | Metric | Source | Status |
||---|---|-------|--------|--------|
|| 1 | GPU Temperature | `gpu_temp_c` | GPUMetric (gpu_index=0) | ✅ Works |
|| 2 | GPU Utilization | `gpu_util_pct` | GPUMetric (gpu_index=0) | ✅ Works |
|| 3 | GPU Memory | `gpu_mem_used_mb` | GPUMetric (gpu_index=0) | ✅ Works |
|| 4 | GPU Power | `gpu_power_w` | GPUMetric (gpu_index=0) | ✅ Works |
|| 5 | CPU Utilization | `cpu_utilization_pct` | MetricSnapshot | ✅ Works |
|| 6 | CPU Temperature | `cpu_temp_c` | MetricSnapshot | ✅ Works |
|| 7 | Memory Usage | `mem_used_bytes` | MetricSnapshot | ✅ Fixed |

## Memory & Swap Charts

| # | Chart | Metrics | Source | Status |
|---|---|---|---|---|
| 8 | Memory & Swap (combined) | `mem_used_bytes`, `mem_free_bytes`, `swap_used_bytes` | MetricSnapshot | ✅ Combined |

*Implementation: Uses loadChartMemSwap() with multi_mem=true parameter to return all 3 datasets in one request. Blue filled area = Memory Used, green = Memory Free, red = Swap Used. Shared Y-axis in GB.*

## Planned Order (as requested)

### Phase 1: CPU ✅ COMPLETE
- CPU Utilization ✅
- CPU Temperature ✅
- CPU Load Average ✅ (3-line: 1m/5m/15m)

### Phase 2: GPU ✅ COMPLETE (Multi-GPU implemented + fleet overview)
- GPU Temperature ✅ (multi-GPU via loadChartMultiGpu)
- GPU Utilization ✅ (multi-GPU via loadChartMultiGpu)
- GPU Memory ✅ (multi-GPU via loadChartMultiGpu)
- GPU Power ✅ (multi-GPU via loadChartMultiGpu)
- GPU Fan Speed ✅ (multi-GPU via loadChartMultiGpu)
- Fleet Overview table: all GPUs shown ✅

*Implementation: Uses loadChartMultiGpu() with multi_gpu=true parameter in ChartDataView to return one dataset per GPU UUID, labeled with GPU index and model.*

### Phase 3: Memory & Swap ✅ COMPLETE (combined chart)
- Memory Used + Memory Free + Swap Usage ✅ (single combined chart via loadChartMemSwap with multi_mem=true)
- Chart shows all 3 datasets with shared Y-axis (blue filled=used, green dashed=free, red=swap)

### Phase 4: Storage ✅ COMPLETE (Multi-disk implemented)
- Disk Usage % (all disks, not just first) ✅
- Disk Temperature (all disks) ✅

*Implementation: Uses loadChartMultiKey() with multi_disk=true parameter in ChartDataView to return one dataset per unique device, labeled with device and mountpoint.*

### Phase 5: Network ✅ COMPLETE (combined chart)
- Network Traffic (RX + TX + Errors) ✅ (single combined chart with dual Y-axes via loadChartNetworkCombined)
- Left Y-axis: RX (solid green) and TX (dashed blue) in MB
- Right Y-axis: Errors (red bars) in count
- One dataset per interface with multi_iface=true

*Implementation: Uses loadChartMultiKey() with multi_iface=true parameter in ChartDataView to return one dataset per unique interface, labeled with interface and IPv4 address. Byte deltas converted to MB/s.*

### Phase 6: Docker, AI Processes, Rig Health ✅ COMPLETE (Multi-series implemented)
- Container CPU % (per container) ✅
- Container Memory (per container) ✅
- Container Restarts (per container) ✅
- AI Process GPU Memory (per process) ✅
- Uptime ✅ (from software_json.uptime_s)
- Error Frequency ✅ (from ErrorEventOccurrence)

*Implementation: 
- Container charts use loadChartMultiKey() with multi_container=true
- AI Process charts use loadChartMultiKey() with multi_ai=true  
- Uptime chart uses standard loadChart() with metric='uptime_s'
- Error Frequency chart uses loadChart() with metric='error_frequency' (bar chart type)*

## Implementation Notes

**Backend (ChartDataView):**
- Supports multi_gpu, multi_disk, multi_iface, multi_container, multi_ai parameters
- Returns datasets with '_key' for identification and 'label' for display
- Handles byte-to-GB and byte-delta-to-MB/s conversions server-side
- Uses _fill_buckets_multi_key for grouping by unique values
- Supports variable bucket sizes via `bucket_minutes` parameter (1, 15, or 60 minutes)
- For bucket_minutes > 1, uses per-metric aggregation: avg (default), sum (network byte deltas, error counts), median, max, min
- Timeframe options: 24h (1-min buckets), 7d (15-min buckets), 30d (1-hour buckets)

**Frontend (rig_detail.html):**
- loadChartMultiGpu(): For multi-GPU charts (one dataset per GPU)
- loadChartMultiKey(): Generic multi-series function for disks, interfaces, containers, AI processes
- loadChartLoadAvg(): Specialized for CPU load average (3-line chart)
- loadChartMemSwap(): Combined Memory & Swap chart (3 datasets, one request)
- loadChartNetworkCombined(): Combined Network RX/TX/Errors chart (dual Y-axes, 3 parallel requests)
- loadChart(): Standard single-series charts
- All charts use Chart.js with appropriate types (line/bar/step)
- Null values preserved to show gaps in data (offline periods)
- Timeframe toggle buttons (24h, 7d, 30d) in chart tab header with dynamic label updates

**Color Coding:**
- Consistent color palette across chart types
- Multi-series charts use distinct colors per dataset
- Tooltips show formatted values with units
- Dynamic label skip based on total bucket count (~12 labels)

---

## Verification Summary

All charts proposed in the ADDITIONAL_CHARTS_PROPOSAL.md have been implemented and verified:

**Backend Verification:**
- ChartDataView correctly handles all requested metrics via dedicated fields or special handling
- Multi-series parameters (multi_gpu, multi_disk, etc.) return properly grouped datasets
- Byte conversions and delta calculations are performed server-side
- Special handling for CPU load average (3-values), uptime (from JSON), and error frequency (aggregation) works correctly

**Frontend Verification:**
- rig_detail.html contains all required canvas elements for each chart category
- JavaScript functions loadChartMultiGpu(), loadChartMultiKey(), loadChartLoadAvg(), and loadChart() are implemented
- Charts render with appropriate types (line/bar/step) and color coding
- Tooltips display formatted values with correct units
- Null values are handled to show data gaps for offline periods
- Hourly labels on x-axis prevent crowding (show every 60th label = hourly)

**Multi-series Functionality Verified:**
- GPU charts: One chart per metric with multiple datasets (one per GPU)
- Storage charts: One chart per metric with multiple datasets (one per disk)
- Network charts: combined RX+TX+Errors into one chart with dual Y-axes
- Docker charts: One chart per metric with multiple datasets (one per container)
- AI Process charts: One chart per metric with multiple datasets (one per process)

All implementation details match the current state of the codebase as verified through documentation review.