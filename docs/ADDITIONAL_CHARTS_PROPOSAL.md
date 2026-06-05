# Additional Charts — Analysis & Proposal

## Currently Plugged Charts (7 charts)

| # | Chart | Metric | Source Table | Already Works? |
|---|-------|--------|-------------|----------------|
| 1 | GPU Temperature | `gpu_temp_c` | GPUMetric | ✅ |
| 2 | GPU Utilization | `gpu_util_pct` | GPUMetric | ✅ |
| 3 | GPU Memory | `gpu_mem_used_mb` | GPUMetric | ✅ |
| 4 | GPU Power | `gpu_power_w` | GPUMetric | ✅ |
| 5 | CPU Utilization | `cpu_utilization_pct` | MetricSnapshot | ✅ |
| 6 | CPU Temperature | `cpu_temp_c` | MetricSnapshot | ✅ |
| 7 | Memory Usage | `mem_used_bytes` | MetricSnapshot | ✅ |

## Available Time-Series Data NOT Currently Charted

### Category: GPU (per-GPU, gpu_index=0 currently)

| Metric | Field | Unit | Diagnostic Value | Priority |
|--------|-------|------|-----------------|----------|
| GPU VRAM Total | `GPUMetric.mem_total_mb` | MB | Shows if GPU memory changed (hardware swap). Static line = healthy. | LOW |
| GPU VRAM Free | `GPUMetric.mem_free_mb` | MB | Inverse of used. Same info as GPU Memory chart. | SKIP |
| GPU VRAM Utilization | `GPUMetric.mem_util_pct` | % | More intuitive than raw MB. Shows memory pressure. | **HIGH** |
| GPU Fan Speed | `GPUMetric.fan_speed_pct` | % | Fan stuck at 0% = cooling problem. Fan at 100% = overheating. | **HIGH** |
| GPU Power Limit | `GPUMetric.power_limit_w` | W | Shows power cap. Static line. Useful to see if power limit changed. | LOW |

### Category: CPU

| Metric | Field | Unit | Diagnostic Value | Priority |
|--------|-------|------|-----------------|----------|
| CPU Load Average (1/5/15 min) | `MetricSnapshot.cpu_load_avg_json` | load | Classic Unix load metric. > core count = overloaded. 3 lines on one chart. | **HIGH** |
| Memory Free | `MetricSnapshot.mem_free_bytes` | GB | Inverse of used. Same info. | SKIP |
| Memory Cached | `MetricSnapshot.mem_cached_bytes` | GB | Shows cache pressure. Sudden drop = something consumed cache. | MEDIUM |
| Swap Used | `MetricSnapshot.swap_used_bytes` | GB | Swap growing = memory leak or insufficient RAM. Critical diagnostic. | **HIGH** |
| Swap Total | `MetricSnapshot.swap_total_bytes` | GB | Static. Shows swap configuration. | LOW |

### Category: Storage (per-device)

| Metric | Field | Unit | Diagnostic Value | Priority |
|--------|-------|------|-----------------|----------|
| Disk Usage % (all disks) | `StorageMetric.usage_pct` | % | Only first disk currently. Showing all disks is critical — any disk filling up is a problem. | **HIGH** |
| Disk Temperature | `StorageMetric.temp_c` | °C | Overheating disk = hardware failure risk. | **HIGH** |
| Disk Capacity | `StorageMetric.capacity_bytes` | GB | Static. Useful as reference line. | LOW |

### Category: Network (per-interface)

| Metric | Field | Unit | Diagnostic Value | Priority |
|--------|-------|------|-----------------|----------|
| Network RX Rate | `NetworkMetric.rx_bytes_delta` | MB/s | Traffic throughput. Spikes = data transfer. Flatline = no network activity. | **HIGH** |
| Network TX Rate | `NetworkMetric.tx_bytes_delta` | MB/s | Same for transmit. | **HIGH** |
| Network RX Errors | `NetworkMetric.rx_errors` | count | Errors = cable/NIC problem. Should always be 0 or flat. | **HIGH** |
| Network TX Errors | `NetworkMetric.tx_errors` | count | Same for transmit. | **HIGH** |
| Network Link Speed | `NetworkMetric.link_speed_mbps` | Mbps | Static. Shows negotiated link speed. | LOW |

### Category: Docker (per-container)

| Metric | Field | Unit | Diagnostic Value | Priority |
|--------|-------|------|-----------------|----------|
| Container CPU % | `DockerContainerMetric.cpu_pct` | % | Per-container resource usage. Identifies which container is hogging CPU. | **HIGH** |
| Container Memory | `DockerContainerMetric.mem_usage_bytes` | GB | Per-container memory. Identifies memory leaks in containers. | **HIGH** |
| Container Memory Limit | `DockerContainerMetric.mem_limit_bytes` | GB | Shows container memory cap. | MEDIUM |
| Container Restarts | `DockerContainerMetric.restart_count` | count | Increasing = container crashing. Critical diagnostic. | **HIGH** |

### Category: AI Processes (per-process)

| Metric | Field | Unit | Diagnostic Value | Priority |
|--------|-------|------|-----------------|----------|
| Process GPU Memory | `AIProcessMetric.gpu_mem_used_mb` | MB | Which process uses how much GPU memory. Stacked bar = total GPU memory breakdown. | **HIGH** |
| Process CPU % | `AIProcessMetric.cpu_pct` | % | Per-process CPU usage. | MEDIUM |

### Category: Rig Health

| Metric | Field | Unit | Diagnostic Value | Priority |
|--------|-------|------|-----------------|----------|
| Uptime | `MetricSnapshot.software_json.uptime_s` | days | Resets = reboot. Frequent reboots = instability. | **HIGH** |
| Rig Status Events | `RigStatusEvent.status` | enum | Timeline of online/stale/offline transitions. | **HIGH** |
| Error Frequency | `ErrorEventOccurrence.timestamp` | count/min | Errors per minute over time. Spikes = problems. | **HIGH** |

## Recommended New Charts (in priority order)

### Tier 1 — High Diagnostic Value (implement first)

| # | Chart | Source | Type | Why |
|---|-------|--------|------|-----|
| 1 | **CPU Load Average** (1/5/15 min) | MetricSnapshot | 3-line chart | Classic overload indicator. 3 lines show trend. |
| 2 | **Swap Usage** | MetricSnapshot | Area chart | Memory leak detector. Should be flat at 0. |
| 3 | **GPU VRAM Utilization %** | GPUMetric | Line chart | More intuitive than raw MB. Shows memory pressure. |
| 4 | **GPU Fan Speed** | GPUMetric | Line chart | Cooling health. 0% = fan failure. 100% = overheating. |
| 5 | **Network RX/TX Rate** | NetworkMetric | 2-line chart | Traffic throughput. Identifies network bottlenecks. |
| 6 | **Network Errors** | NetworkMetric | Bar chart | Cable/NIC problems. Should be 0. |
| 7 | **Disk Temperature** (all disks) | StorageMetric | Multi-line chart | Overheating disk = hardware failure risk. |
| 8 | **Disk Usage %** (all disks) | StorageMetric | Multi-line chart | Any disk filling up = problem. Currently only first disk shown. |
| 9 | **Container CPU %** (per container) | DockerContainerMetric | Multi-line chart | Which container hogs CPU. |
| 10 | **Container Memory** (per container) | DockerContainerMetric | Multi-line chart | Memory leak detection per container. |
| 11 | **Container Restarts** | DockerContainerMetric | Step chart | Increasing = crashing container. |
| 12 | **Uptime** | MetricSnapshot | Step chart | Resets = reboots. Frequent reboots = instability. |
| 13 | **Error Frequency** | ErrorEventOccurrence | Bar chart | Errors per time window. Spikes = problems. |
| 14 | **AI Process GPU Memory** (stacked) | AIProcessMetric | Stacked bar | GPU memory breakdown by process. |

### Tier 2 — Medium Value (implement if time permits)

| # | Chart | Source | Type | Why |
|---|-------|--------|------|-----|
| 15 | **Memory Cached** | MetricSnapshot | Area chart | Cache pressure indicator. |
| 16 | **Process CPU %** | AIProcessMetric | Multi-line chart | Per-process CPU breakdown. |
| 17 | **Container Memory Limit** | DockerContainerMetric | Line chart | Reference line for container memory cap. |

### Tier 3 — Low Value (nice to have)

| # | Chart | Source | Type | Why |
|---|-------|--------|------|-----|
| 18 | **GPU VRAM Total** | GPUMetric | Line chart | Static reference. Detects GPU swap. |
| 19 | **GPU Power Limit** | GPUMetric | Line chart | Static reference. Detects power cap change. |
| 20 | **Swap Total** | MetricSnapshot | Line chart | Static reference. |
| 21 | **Disk Capacity** | StorageMetric | Line chart | Static reference per disk. |
| 22 | **Network Link Speed** | NetworkMetric | Line chart | Static reference. Detects link downgrade. |

## Implementation Notes

### ChartDataView changes needed:
- Currently supports: `cpu_utilization_pct`, `cpu_temp_c`, `mem_used_bytes`, `mem_total_bytes`, `gpu_temp_c`, `gpu_util_pct`, `gpu_mem_used_mb`, `gpu_mem_total_mb`, `gpu_power_w`, `gpu_power_limit_w`, `gpu_fan_pct`, `disk_usage_pct`
- Needs to add: `mem_free_bytes`, `mem_cached_bytes`, `swap_used_bytes`, `swap_total_bytes`, `mem_util_pct` (GPU), `fan_speed_pct` (GPU), `rx_bytes_delta`, `tx_bytes_delta`, `rx_errors`, `tx_errors`, `disk_temp_c`, `cpu_load_avg_json`

### New query patterns needed:
- **Per-container charts**: Query DockerContainerMetric grouped by container name
- **Per-disk charts**: Query StorageMetric grouped by device
- **Per-interface charts**: Query NetworkMetric grouped by interface
- **Per-process charts**: Query AIProcessMetric grouped by process_name
- **Load average**: Special handling — 3 values per timestamp (1min, 5min, 15min)
- **Uptime**: Read from software_json.uptime_s (JSON field)
- **Error frequency**: Aggregate ErrorEventOccurrence by time bucket
- **Rig status**: Timeline of RigStatusEvent transitions

### Frontend changes needed:
- New canvas elements in rig_detail.html
- New loadChart() calls with appropriate colors
- Some charts need multi-series support (load avg, per-container, per-disk, per-interface)
- Error frequency needs bar chart type instead of line
- Uptime needs step chart type
