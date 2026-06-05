# Additional Charts — Analysis & Proposal

## Currently Plugged Charts (7 charts)

|| # | Chart | Metric | Source Table | Already Works? |
|---|---|-------|-------------|----------------|
| 1 | GPU Temperature | `gpu_temp_c` | GPUMetric | ✅ |
| 2 | GPU Utilization | `gpu_util_pct` | GPUMetric | ✅ |
| 3 | GPU Memory | `gpu_mem_used_mb` | GPUMetric | ✅ |
| 4 | GPU Power | `gpu_power_w` | GPUMetric | ✅ |
| 5 | CPU Utilization | `cpu_utilization_pct` | MetricSnapshot | ✅ |
| 6 | CPU Temperature | `cpu_temp_c` | MetricSnapshot | ✅ |
| 7 | Memory Usage | `mem_used_bytes` | MetricSnapshot | ✅ |

## Available Time-Series Data NOW Charted (Previously Not Charted)

### Category: GPU (per-GPU, gpu_index=0 currently, now multi-GPU supported)

|| Metric | Field | Unit | Diagnostic Value | Priority | Status |
|--------|-------|-------|------|-----------------|----------|--------|
| GPU VRAM Total | `GPUMetric.mem_total_mb` | MB | Shows if GPU memory changed (hardware swap). Static line = healthy. | LOW | ✅ (via multi_gpu) |
| GPU VRAM Free | `GPUMetric.mem_free_mb` | MB | Inverse of used. Same info as GPU Memory chart. | SKIP | ✅ (via multi_gpu) |
| GPU VRAM Utilization | `GPUMetric.mem_util_pct` | % | More intuitive than raw MB. Shows memory pressure. | **HIGH** | ✅ (via multi_gpu) |
| GPU Fan Speed | `GPUMetric.fan_speed_pct` | % | Fan stuck at 0% = cooling problem. Fan at 100% = overheating. | **HIGH** | ✅ (via multi_gpu) |
| GPU Power Limit | `GPUMetric.power_limit_w` | W | Shows power cap. Static line. Useful to see if power limit changed. | LOW | ✅ (via multi_gpu) |

### Category: CPU

|| Metric | Field | Unit | Diagnostic Value | Priority | Status |
|--------|-------|-------|------|-----------------|----------|--------|
| CPU Load Average (1/5/15 min) | `MetricSnapshot.cpu_load_avg_json` | load | Classic Unix load metric. > core count = overloaded. 3 lines on one chart. | **HIGH** | ✅ (3-line chart) |
| Memory Free | `MetricSnapshot.mem_free_bytes` | GB | Inverse of used. Same info. | SKIP | ✅ |
| Memory Cached | `MetricSnapshot.mem_cached_bytes` | GB | Shows cache pressure. Sudden drop = something consumed cache. | MEDIUM | ✅ |
| Swap Used | `MetricSnapshot.swap_used_bytes` | GB | Swap growing = memory leak or insufficient RAM. Critical diagnostic. | **HIGH** | ✅ |
| Swap Total | `MetricSnapshot.swap_total_bytes` | GB | Static. Shows swap configuration. | LOW | ✅ |

### Category: Storage (per-device)

|| Metric | Field | Unit | Diagnostic Value | Priority | Status |
|--------|-------|-------|------|-----------------|----------|--------|
| Disk Usage % (all disks) | `StorageMetric.usage_pct` | % | Only first disk previously. Showing all disks is critical — any disk filling up is a problem. | **HIGH** | ✅ (multi_disk) |
| Disk Temperature | `StorageMetric.temp_c` | °C | Overheating disk = hardware failure risk. | **HIGH** | ✅ (multi_disk) |
| Disk Capacity | `StorageMetric.capacity_bytes` | GB | Static. Useful as reference line. | LOW | ✅ (multi_disk) |

### Category: Network (per-interface)

|| Metric | Field | Unit | Diagnostic Value | Priority | Status |
|--------|-------|-------|------|-----------------|----------|--------|
| Network RX Rate | `NetworkMetric.rx_bytes_delta` | MB/s | Traffic throughput. Spikes = data transfer. Flatline = no network activity. | **HIGH** | ✅ (multi_iface) |
| Network TX Rate | `NetworkMetric.tx_bytes_delta` | MB/s | Same for transmit. | **HIGH** | ✅ (multi_iface) |
| Network RX Errors | `NetworkMetric.rx_errors` | count | Errors = cable/NIC problem. Should always be 0 or flat. | **HIGH** | ✅ (multi_iface) |
| Network TX Errors | `NetworkMetric.tx_errors` | count | Same for transmit. | **HIGH** | ✅ (multi_iface) |
| Network Link Speed | `NetworkMetric.link_speed_mbps` | Mbps | Static. Shows negotiated link speed. | LOW | ✅ (multi_iface) |

### Category: Docker (per-container)

|| Metric | Field | Unit | Diagnostic Value | Priority | Status |
|--------|-------|-------|------|-----------------|----------|--------|
| Container CPU % | `DockerContainerMetric.cpu_pct` | % | Per-container resource usage. Identifies which container is hogging CPU. | **HIGH** | ✅ (multi_container) |
| Container Memory | `DockerContainerMetric.mem_usage_bytes` | GB | Per-container memory. Identifies memory leaks in containers. | **HIGH** | ✅ (multi_container) |
| Container Memory Limit | `DockerContainerMetric.mem_limit_bytes` | GB | Shows container memory cap. | MEDIUM | ✅ (multi_container) |
| Container Restarts | `DockerContainerMetric.restart_count` | count | Increasing = container crashing. Critical diagnostic. | **HIGH** | ✅ (multi_container) |

### Category: AI Processes (per-process)

|| Metric | Field | Unit | Diagnostic Value | Priority | Status |
|--------|-------|-------|------|-----------------|----------|--------|
| Process GPU Memory | `AIProcessMetric.gpu_mem_used_mb` | MB | Which process uses how much GPU memory. Stacked bar = total GPU memory breakdown. | **HIGH** | ✅ (multi_ai) |
| Process CPU % | `AIProcessMetric.cpu_pct` | % | Per-process CPU usage. | MEDIUM | ✅ (multi_ai) |

### Category: Rig Health

|| Metric | Field | Unit | Diagnostic Value | Priority | Status |
|--------|-------|-------|------|-----------------|----------|--------|
| Uptime | `MetricSnapshot.software_json.uptime_s` | days | Resets = reboot. Frequent reboots = instability. | **HIGH** | ✅ |
| Rig Status Events | `RigStatusEvent.status` | enum | Timeline of online/stale/offline transitions. | **HIGH** | ✅ |
| Error Frequency | `ErrorEventOccurrence.timestamp` | count/min | Errors per minute over time. Spikes = problems. | **HIGH** | ✅ (bar chart) |

## Implemented New Charts (in priority order)

### Tier 1 — High Diagnostic Value (All implemented)

|| # | Chart | Source | Type | Implemented? |
|---|---|--------|------|--------------|
| 1 | **CPU Load Average** (1/5/15 min) | MetricSnapshot | 3-line chart | ✅ |
| 2 | **Swap Usage** | MetricSnapshot | Area chart | ✅ |
| 3 | **GPU VRAM Utilization %** | GPUMetric | Line chart (multi-GPU) | ✅ |
| 4 | **GPU Fan Speed** | GPUMetric | Line chart (multi-GPU) | ✅ |
| 5 | **Network RX/TX Rate** | NetworkMetric | 2-line chart (multi-interface) | ✅ |
| 6 | **Network Errors** | NetworkMetric | Bar chart (multi-interface) | ✅ |
| 7 | **Disk Temperature** (all disks) | StorageMetric | Multi-line chart | ✅ |
| 8 | **Disk Usage %** (all disks) | StorageMetric | Multi-line chart | ✅ |
| 9 | **Container CPU %** (per container) | DockerContainerMetric | Multi-line chart | ✅ |
| 10 | **Container Memory** (per container) | DockerContainerMetric | Multi-line chart | ✅ |
| 11 | **Container Restarts** | DockerContainerMetric | Step chart | ✅ |
| 12 | **Uptime** | MetricSnapshot | Step chart | ✅ |
| 13 | **Error Frequency** | ErrorEventOccurrence | Bar chart | ✅ |
| 14 | **AI Process GPU Memory** (stacked) | AIProcessMetric | Stacked bar | ✅ |

### Tier 2 — Medium Value (All implemented)

|| # | Chart | Source | Type | Implemented? |
|---|---|--------|------|--------------|
| 15 | **Memory Cached** | MetricSnapshot | Area chart | ✅ |
| 16 | **Process CPU %** | AIProcessMetric | Multi-line chart | ✅ |
| 17 | **Container Memory Limit** | DockerContainerMetric | Line chart | ✅ |

### Tier 3 — Low Value (All implemented)

|| # | Chart | Source | Type | Implemented? |
|---|---|--------|------|--------------|
| 18 | **GPU VRAM Total** | GPUMetric | Line chart | ✅ |
| 19 | **GPU Power Limit** | GPUMetric | Line chart | ✅ |
| 20 | **Swap Total** | MetricSnapshot | Line chart | ✅ |
| 21 | **Disk Capacity** | StorageMetric | Line chart | ✅ |
| 22 | **Network Link Speed** | NetworkMetric | Line chart | ✅ |

## Implementation Notes

**All charts proposed in this document are now implemented:**

### Backend (ChartDataView):
- Supports all metrics listed above via dedicated fields or special handling
- Multi-series support: multi_gpu, multi_disk, multi_iface, multi_container, multi_ai parameters
- Returns datasets with '_key' for identification and 'label' for display
- Handles byte-to-GB and byte-delta-to-MB/s conversions server-side
- Uses _fill_buckets_multi_key for grouping by unique values
- Special handling for CPU load average (3-values), uptime (from JSON), error frequency (aggregation)

### Frontend (rig_detail.html):
- loadChartMultiGpu(): For multi-GPU charts (Temperature, Utilization, Memory, Power, Fan Speed)
- loadChartMultiKey(): Generic multi-series function for disks, interfaces, containers, AI processes
- loadChartLoadAvg(): Specialized for CPU load average (3-line chart)
- loadChart(): Standard single-series charts (Memory Free, Swap Usage, Uptime, Error Frequency, etc.)
- All charts use Chart.js with appropriate types (line/bar/step)
- Null values preserved to show gaps in data (offline periods)
- Consistent color palette across chart types
- Multi-series charts use distinct colors per dataset
- Tooltips show formatted values with units
- Hourly labels on x-axis to prevent crowding (show every 60th label = hourly)

### Verification:
- All Tier 1 (high diagnostic value) charts are implemented
- All Tier 2 (medium value) charts are implemented  
- All Tier 3 (low value/nice to have) charts are implemented
- Multi-series functionality works for GPU (per-GPU), Storage (per-disk), Network (per-interface), Docker (per-container), and AI Processes (per-process)
