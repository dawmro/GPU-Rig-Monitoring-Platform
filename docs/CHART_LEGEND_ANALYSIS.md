# Chart Legend & Y-Axis Analysis

## All Charts in rig_detail.html

### Charts with `loadChart()` — SINGLE dataset, NO legend, NO y-axis title

These charts use the generic `loadChart()` function which has `legend: { display: false }` and no y-axis title:

| # | Chart | Canvas ID | Unit | Has Legend | Has Y Title |
|---|---|---|---|---|---|
| 1 | GPU Temperature | chartGpuTemp | °C | ❌ (multi-GPU uses loadChartMultiGpu) | ❌ |
| 2 | GPU Utilization | chartGpuUtil | % | ❌ | ❌ |
| 3 | GPU VRAM Usage | chartGpuMem | MB | ❌ | ❌ |
| 4 | GPU Power Draw | chartGpuPower | W | ❌ | ❌ |
| 5 | GPU Fan Speed | chartGpuFan | % | ❌ | ❌ |
| 6 | GPU Core Clock | chartGpuCoreClock | MHz | ❌ | ❌ |
| 7 | GPU Memory Clock | chartGpuMemClock | MHz | ❌ | ❌ |
| 8 | **CPU Utilization** | chartCpuUtil | % | ❌ | ❌ |
| 9 | **CPU Temperature** | chartCpuTemp | °C | ❌ | ❌ |
| 10 | **CPU Frequency** | chartCpuFreq | MHz | ❌ | ❌ |
| 11 | Disk Usage | chartDiskUsage | % | ❌ (uses loadChartMultiKey) | ❌ |
| 12 | Disk Read/Write | chartDiskReadWriteThroughput | MB | ❌ (uses loadChartMultiKeyDual) | ❌ |
| 13 | Disk IOPS | chartDiskReadWriteIops | IOPS | ❌ (uses loadChartMultiKeyDual) | ❌ |
| 14 | Disk Utilization | chartDiskUtilization | % | ❌ (uses loadChartMultiKey) | ❌ |
| 15 | Uptime | chartUptime | days | ❌ | ❌ |
| 16 | Error Frequency | chartErrorFreq | err/min | ❌ | ❌ |

Wait — GPU charts use `loadChartMultiGpu()` which DOES have a legend. Let me re-analyze:

### Charts with `loadChartMultiGpu()` — MULTI dataset (per-GPU), HAS legend

| # | Chart | Canvas ID | Unit | Has Legend | Has Y Title |
|---|---|---|---|---|---|
| 1 | GPU Temperature | chartGpuTemp | °C | ✅ | ❌ |
| 2 | GPU Utilization | chartGpuUtil | % | ✅ | ❌ |
| 3 | GPU VRAM Usage | chartGpuMem | MB | ✅ | ❌ |
| 4 | GPU Power Draw | chartGpuPower | W | ✅ | ❌ |
| 5 | GPU Fan Speed | chartGpuFan | % | ✅ | ❌ |
| 6 | GPU Core Clock | chartGpuCoreClock | MHz | ✅ | ❌ |
| 7 | GPU Memory Clock | chartGpuMemClock | MHz | ✅ | ❌ |

### Charts with `loadChartLoadAvg()` — MULTI dataset, HAS legend, HAS y-title

| # | Chart | Canvas ID | Unit | Has Legend | Has Y Title |
|---|---|---|---|---|---|
| 8 | CPU Load Average | chartCpuLoad | Load | ✅ | ✅ ("Load") |

### Charts with `loadChartMemSwap()` — MULTI dataset, HAS legend, HAS y-title

| # | Chart | Canvas ID | Unit | Has Legend | Has Y Title |
|---|---|---|---|---|---|
| 9 | Memory & Swap | chartMemSwap | GB | ✅ | ✅ ("GB") |

### Charts with `loadChartNetworkCombined()` — MULTI dataset, HAS legend, HAS y-titles

| # | Chart | Canvas ID | Unit | Has Legend | Has Y Title |
|---|---|---|---|---|---|
| 10 | Network | chartNetCombined | MB / Errors | ✅ | ✅ (dual: "MB" + "Errors") |

### Charts with `loadChartMultiKeyDual()` — MULTI dataset, HAS legend, NO y-title

| # | Chart | Canvas ID | Unit | Has Legend | Has Y Title |
|---|---|---|---|---|---|
| 11 | Disk Read/Write | chartDiskReadWriteThroughput | MB | ✅ | ❌ |
| 12 | Disk IOPS | chartDiskReadWriteIops | IOPS | ✅ | ❌ |

### Charts with `loadChartMultiKey()` — MULTI dataset, HAS legend, NO y-title

| # | Chart | Canvas ID | Unit | Has Legend | Has Y Title |
|---|---|---|---|---|---|
| 13 | Disk Usage | chartDiskUsage | % | ✅ | ❌ |
| 14 | Disk Utilization | chartDiskUtilization | % | ✅ | ❌ |

### Charts with `loadChart()` — SINGLE dataset, NO legend, NO y-title

| # | Chart | Canvas ID | Unit | Has Legend | Has Y Title |
|---|---|---|---|---|---|
| 15 | CPU Utilization | chartCpuUtil | % | ❌ | ❌ |
| 16 | CPU Temperature | chartCpuTemp | °C | ❌ | ❌ |
| 17 | CPU Frequency | chartCpuFreq | MHz | ❌ | ❌ |
| 18 | Uptime | chartUptime | days | ❌ | ❌ |
| 19 | Error Frequency | chartErrorFreq | err/min | ❌ | ❌ |

---

## Summary of Problems

### Problem 1: `loadChart()` has NO legend and NO y-axis title
**Affected charts:** CPU Utilization, CPU Temperature, CPU Frequency, Uptime, Error Frequency (5 charts)

These are single-dataset charts. They have:
- `legend: { display: false }` — explicitly hidden
- No y-axis title — just `ticks: { color: '#9ca3af' }` with no label

### Problem 2: `loadChartMultiGpu()` has legend but NO y-axis title
**Affected charts:** All 7 GPU charts

These have:
- `legend: { display: true, ... }` — shows GPU0, GPU1, etc.
- No y-axis title — just `ticks: { color: '#9ca3af' }` with no unit label

### Problem 3: `loadChartMultiKey()` and `loadChartMultiKeyDual()` have legend but NO y-axis title
**Affected charts:** Disk Usage, Disk Utilization, Disk Read/Write, Disk IOPS (4 charts)

These have:
- `legend: { display: true, ... }` — shows disk names
- No y-axis title — just `ticks: { color: '#9ca3af' }` with no unit label

---

## What Works Correctly

The following charts have BOTH legend AND y-axis title:
1. **CPU Load Average** — legend ✅, y-title "Load" ✅
2. **Memory & Swap** — legend ✅, y-title "GB" ✅
3. **Network** — legend ✅, y-titles "MB" + "Errors" ✅ (dual axis)

These were written with custom chart configurations. The generic helper functions (`loadChart`, `loadChartMultiGpu`, `loadChartMultiKey`, `loadChartMultiKeyDual`) are missing y-axis titles.

---

## Plan to Fix

### Fix 1: Add y-axis title to `loadChart()` (5 charts affected)
Add `title: { display: true, text: unit, color: '#6b7280', font: { size: 11 } }` to the y-axis config.
The `unit` parameter is already passed to the function — just need to use it for the title.
Keep legend hidden (single dataset doesn't need a legend).

### Fix 2: Add y-axis title to `loadChartMultiGpu()` (7 charts affected)
Same approach — add y-axis title using the `unit` parameter.
Keep legend as-is (multi-GPU needs it).

### Fix 3: Add y-axis title to `loadChartMultiKey()` (2 charts affected)
Same approach — add y-axis title using the `unit` parameter.
Keep legend as-is (multi-disk needs it).

### Fix 4: Add y-axis title to `loadChartMultiKeyDual()` (2 charts affected)
Same approach — add y-axis title using the `unit` parameter.
Keep legend as-is (multi-disk needs it).

---

## Implementation

All 4 fixes follow the same pattern — add a `title` block to the `y` axis config:

```javascript
y: {
    grid: { color: 'rgba(255,255,255,0.05)' },
    ticks: { color: '#9ca3af' },
    beginAtZero: true,
    title: { display: true, text: unit, color: '#6b7280', font: { size: 11 } }
}
```

The `unit` parameter is already passed to every function. We just need to use it.

### Files to modify:
- `gpu_monitor/templates/dashboard/rig_detail.html` — 4 JavaScript functions

### Estimated effort: ~30 minutes
- Each function needs 1 line added to the y-axis config
- 4 functions × 1 line = 4 lines changed
- Test by loading rig detail page and verifying all charts show y-axis labels
