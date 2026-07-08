# Chart Order Verification - Chart Tab vs Data Fetching

## Issue
The chart loading order in JavaScript (`chartLoaders` array) does NOT match the HTML canvas order in the Charts tab. The comment explicitly states: "This MUST match the HTML canvas order in the page."

## Chart Loaders Array Order (JavaScript - `rig_detail.html` lines 540-567)

| Index | Canvas ID | Metric | Label |
|-------|-----------|--------|-------|
| 1 | chartGpuTemp | gpu_temp_c | GPU Temperature |
| 2 | chartGpuUtil | gpu_util_pct | GPU Utilization |
| 3 | chartGpuMemCtrlUtil | gpu_mem_controller_util_pct | GPU Memory Controller Util |
| 4 | chartGpuFan | gpu_fan_pct | GPU Fan Speed |
| 5 | chartGpuPower | gpu_power_w | GPU Power Draw |
| 6 | chartGpuMem | gpu_mem_used_mb | GPU VRAM Usage |
| 7 | chartGpuCoreClock | gpu_core_clock_mhz | GPU Core Clock |
| 8 | chartGpuMemClock | gpu_mem_clock_mhz | GPU Memory Clock |
| 9 | chartCpuUtil | cpu_utilization_pct | CPU Utilization |
| 10 | chartCpuTemp | cpu_temp_c | CPU Temperature |
| 11 | chartCpuFreq | cpu_freq_current_mhz | CPU Frequency |
| 12 | chartCpuPower | cpu_power_w | CPU Power |
| 13 | chartCpuLoad | cpu_load_avg | CPU Load Average |
| 14 | chartDiskUsage | disk_usage_pct | Disk Usage |
| 15 | chartDiskReadWriteThroughput | disk_read/write_bytes_delta | Disk Read/Write |
| 16 | chartDiskReadWriteIops | disk_read/write_iops_delta | Disk IOPS |
| 17 | chartDiskUtilization | disk_utilization_pct | Disk Utilization |
| 18 | chartTotalPower | total_system_power_w | Total System Power |
| 19 | chartMemSwap | mem_swap | Memory/Swap |
| 20 | chartNetCombined | net_rx/tx_bytes_delta | Network |
| 21 | chartUptime | uptime_s | Uptime |
| 22 | chartErrorFreq | error_frequency | Error Frequency |

## HTML Canvas Order (Actual Page Render Order)

| Index | Canvas ID | Label |
|-------|-----------|-------|
| 1 | chartGpuTemp | GPU Temperature |
| 2 | chartGpuFan | GPU Fan Speed |
| 3 | chartGpuUtil | GPU Utilization |
| 4 | chartGpuMemCtrlUtil | GPU Memory Controller Util |
| 4 | chartGpuPower | GPU Power Draw |
| 5 | chartGpuMem | GPU VRAM Usage |
| 6 | chartGpuCoreClock | GPU Core Clock |
| 7 | chartGpuMemClock | GPU Memory Clock |
| 8 | chartCpuUtil | CPU Utilization |
| 9 | chartCpuTemp | CPU Temperature |
| 10 | chartCpuFreq | CPU Frequency |
| 10 | chartCpuPower | CPU Power |
| 10 | chartCpuLoad | CPU Load Average |
| 11 | chartDiskUsage | Disk Usage |
| 12 | chartDiskReadWriteThroughput | Disk Read/Write |
| 13 | chartDiskReadWriteIops | Disk IOPS |
| 13 | chartDiskUtilization | Disk Utilization |
| 14 | chartTotalPower | Total System Power |
| 15 | chartMemSwap | Memory/Swap |
| 15 | chartNetCombined | Network |
| 15 | chartUptime | Uptime |
| 15 | chartErrorFreq | Error Frequency |

## Discrepancies Found

| Chart | JS Loaders Order | HTML Canvas Order | Mismatch? |
|-------|------------------|-------------------|-----------|
| GPU Fan | 4th | 2nd | ✅ YES |
| GPU Util | 2nd | 3rd | ✅ YES |
| Mem Ctrl | 3rd | 4th | ✅ YES |

The **Fan** and **Util** charts are swapped between JS loader order and HTML canvas order.

## Root Cause
The comment in the code says: "This MUST match the HTML canvas order in the page. To reorder charts on the page, reorder both the HTML canvas elements AND this array."

But they are NOT in sync - the `chartLoaders` array has a different order than the HTML canvas elements.

## Impact
- Charts may load in wrong order (visual jumping)
- Staggered loading delay may not match visual flow
- User sees charts appear in different order than they render

## Fix Required
Either:
1. Reorder `chartLoaders` array to match HTML canvas order, OR
2. Reorder HTML canvas elements to match `chartLoaders` array

The correct approach is to make them match. Since the HTML defines the visual layout, the `chartLoaders` array should be reordered to match the HTML canvas order.

### Corrected chartLoaders Order (to match HTML)
```javascript
var chartLoaders = [
    // GPU charts
    function() { return loadChartMultiGpu('chartGpuTemp',      'gpu_temp_c',        uuid, range, '°C'); },
    function() { return loadChartMultiGpu('chartGpuFan',       'gpu_fan_pct',       uuid, range, '%'); },
    function() { return loadChartMultiGpu('chartGpuUtil',      'gpu_util_pct',      uuid, range, '%'); },
    function() { return loadChartMultiGpu('chartGpuMemCtrlUtil', 'gpu_mem_controller_util_pct', uuid, range, '%'); },
    function() { return loadChartMultiGpu('chartGpuPower',     'gpu_power_w',       uuid, range, 'W'); },
    function() { return loadChartMultiGpu('chartGpuMem',       'gpu_mem_used_mb',   uuid, range, ' MB'); },
    function() { return loadChartMultiGpu('chartGpuCoreClock', 'gpu_core_clock_mhz', uuid, range, ' MHz'); },
    function() { return loadChartMultiGpu('chartGpuMemClock',  'gpu_mem_clock_mhz',  uuid, range, ' MHz'); },
    // ... rest unchanged
```

## Additional Verification Needed
- Verify the GPU_METRICS mapping in `metrics_app/views.py` matches chart metric names
- Verify all canvas IDs in HTML match the IDs used in chartLoaders
- Verify multi_gpu parameter is correctly used for multi-GPU charts