# Live Metrics Detail Display — Plan

## Goal
Display ALL collected data in the Live Metrics tab, organized by category with full detail.
Each section shows per-device data (multiple GPUs, multiple disks, multiple network interfaces, etc.)

---

## Data Available Per Category

### 1. CPU
| Field | Source | Display |
|-------|--------|---------|
| Model | `MetricSnapshot.cpu_model` | "AMD Ryzen 7 5700X3D 8-Core Processor" |
| Physical cores | `MetricSnapshot.cpu_physical_cores` | "8 cores" |
| Logical cores | `MetricSnapshot.cpu_logical_cores` | "16 threads" |
| Utilization | `MetricSnapshot.cpu_utilization_pct` | "5.2%" (large, color-coded) |
| Temperature | `MetricSnapshot.cpu_temp_c` | "45°C" (color-coded thresholds) |
| Load average | `cpu.load_avg` (from metrics JSON) | "0.26 / 0.23 / 0.36" |

### 2. Memory
| Field | Source | Display |
|-------|--------|---------|
| Total | `MetricSnapshot.mem_total_bytes` | "64.0 GB" |
| Used | `MetricSnapshot.mem_used_bytes` | "24.8 GB" |
| Free | `memory.free_bytes` (from metrics JSON) | "39.1 GB" |
| Cached | `MetricSnapshot.mem_cached_bytes` | "3.6 GB" |
| Usage bar | used/total | Progress bar with % |
| Swap used | `memory.swap_used_bytes` (from metrics JSON) | "337 MB" |
| Swap total | `memory.swap_total_bytes` (from metrics JSON) | "8.0 GB" |

### 3. Storage (per device, multiple disks)
| Field | Source | Display |
|-------|--------|---------|
| Device | `StorageMetric.device` | "/dev/sda2" or "C:\" |
| Mountpoint | `StorageMetric.mountpoint` | "/" or "C:\" |
| Filesystem | `StorageMetric.fstype` | "ext4" / "NTFS" |
| Capacity | `StorageMetric.capacity_bytes` | "29.4 GB" |
| Usage | `StorageMetric.usage_pct` | "62.4%" (progress bar) |
| Temperature | `StorageMetric.temp_c` | "42°C" or "—" |
| SMART health | `StorageMetric.smart_health` | "OK" / "FAILING" / "—" |

### 4. GPU (per device, multiple GPUs)
| Field | Source | Display |
|-------|--------|---------|
| Model | `GPUMetric.model` | "NVIDIA GeForce RTX 3060" |
| UUID | `GPUMetric.gpu_uuid` | "GPU-a322cff7-..." (truncated) |
| Utilization | `GPUMetric.gpu_util_pct` | "94.0%" (large, color-coded) |
| Temperature | `GPUMetric.gpu_temp_c` | "45°C" (color-coded thresholds) |
| Fan speed | `GPUMetric.fan_speed_pct` | "0%" or "65%" |
| VRAM total | `GPUMetric.mem_total_mb` | "12288 MB" |
| VRAM used | `GPUMetric.mem_used_mb` | "1137 MB" |
| VRAM free | `GPUMetric.mem_free_mb` | "11150 MB" |
| VRAM util | `GPUMetric.mem_util_pct` | "9.3%" |
| Power draw | `GPUMetric.power_draw_w` | "8.8W" |
| Power limit | `GPUMetric.power_limit_w` | "170W" |

### 5. Network (per interface)
| Field | Source | Display |
|-------|--------|---------|
| Interface | `NetworkMetric.interface` | "Ethernet" / "ens33" |
| IPv4 | `NetworkMetric.ipv4` | "192.168.253.131" |
| Link speed | `NetworkMetric.link_speed_mbps` | "1000 Mbps" |
| RX bytes | `NetworkMetric.rx_bytes` | "133.3 GB" (total) |
| TX bytes | `NetworkMetric.tx_bytes` | "7.8 GB" (total) |
| RX errors | `NetworkMetric.rx_errors` | "0" |
| TX errors | `NetworkMetric.tx_errors` | "0" |

### 6. Motherboard
| Field | Source | Display |
|-------|--------|---------|
| Manufacturer | `MetricSnapshot.motherboard_json.manufacturer` | "Gigabyte Technology Co., Ltd." |
| Model | `MetricSnapshot.motherboard_json.model` | "B450M DS3H-CF" |
| BIOS version | `MetricSnapshot.motherboard_json.bios_version` | "F67d" |

### 7. Software / OS
| Field | Source | Display |
|-------|--------|---------|
| Hostname | `MetricSnapshot.software_json.hostname` | "DESKTOP-REE04FV" |
| OS distro | `MetricSnapshot.software_json.os_distro` | "Windows-10-10.0.19045-SP0" |
| Kernel | `MetricSnapshot.software_json.kernel` | "10" |
| Uptime | `MetricSnapshot.software_json.uptime_s` | "20d 15h 32m" |
| NVIDIA driver | `MetricSnapshot.software_json.nvidia_driver` | "571.96" |
| Docker version | `MetricSnapshot.software_json.docker_version` | "24.0.7" |

### 8. Docker Containers (per container)
| Field | Source | Display |
|-------|--------|---------|
| Name | `DockerContainerMetric.name` | "ollama" |
| Image | `DockerContainerMetric.image` | "ollama/ollama:latest" |
| Status | `DockerContainerMetric.status` | "running" / "exited" |
| Restarts | `DockerContainerMetric.restart_count` | "0" |

### 9. Errors
| Field | Source | Display |
|-------|--------|---------|
| Source | `ErrorEvent.source` | "kernel" / "Service" |
| Message | `ErrorEvent.message` | Full message (truncated in list) |
| Last seen | `ErrorEvent.last_seen` | "5 min ago" |
| Count | `ErrorEvent.count` | "×3" |

---

## Proposed Layout

Replace the current 6-card grid with a **section-based layout** organized by category.
Each section is a collapsible card with a table of per-device data.

```
┌─────────────────────────────────────────────────────────┐
│  CPU                                                    │
│  ┌─────────────────────────────────────────────────┐    │
│  │ AMD Ryzen 7 5700X3D 8-Core Processor            │    │
│  │ 8 cores / 16 threads                             │    │
│  │ [████████████░░░░░░░░] 5.2% utilization          │    │
│  │ Temp: 45°C  │  Load: 0.26 / 0.23 / 0.36         │    │
│  └─────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│  Memory                                                 │
│  ┌─────────────────────────────────────────────────┐    │
│  │ [████████████████████░░░░░░░░░░░░] 38.2%        │    │
│  │ Used: 24.8 GB / Total: 64.0 GB                   │    │
│  │ Free: 39.1 GB  │  Cached: 3.6 GB                 │    │
│  │ Swap: 337 MB / 8.0 GB                            │    │
│  └─────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│  Storage (2 devices)                                    │
│  ┌─────────────────────────────────────────────────┐    │
│  │ /dev/sda2  │  /       │  ext4  │  29.4 GB       │    │
│  │ [████████████████████░░░░░░] 62.4%              │    │
│  │ Temp: —  │  SMART: —                            │    │
│  ├─────────────────────────────────────────────────┤    │
│  │ C:\        │  C:\     │  NTFS  │  1000.2 GB     │    │
│  │ [████████████████████████░░] 84.9%              │    │
│  │ Temp: 42°C  │  SMART: OK                        │    │
│  └─────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│  GPU (1 device)                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │ NVIDIA GeForce RTX 3060                          │    │
│  │ UUID: GPU-a322cff7-...                           │    │
│  │ [░░░░░░░░░░░░░░░░░░░░] 0.0% utilization          │    │
│  │ Temp: 45°C  │  Fan: 0%                           │    │
│  │ VRAM: 1137 / 12288 MB (9.3%)                     │    │
│  │ Power: 8.8W / 170W                               │    │
│  └─────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│  Network (4 interfaces)                                 │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Interface    │ IPv4           │ Speed  │ RX/TX   │    │
│  │ Ethernet     │ 192.168.8.158  │ 100M   │ 133G/8G │    │
│  │ VMware VMnet1│ 192.168.40.1   │ 100M   │ 55B/2K  │    │
│  │ VMware VMnet8│ 192.168.253.1  │ 100M   │ 681B/7K │    │
│  │ Loopback     │ 127.0.0.1      │ 100M   │ 0/0     │    │
│  └─────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│  Motherboard                                            │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Gigabyte Technology Co., Ltd.                    │    │
│  │ B450M DS3H-CF  │  BIOS: F67d                     │    │
│  └─────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│  Software                                               │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Hostname: DESKTOP-REE04FV                        │    │
│  │ OS: Windows-10-10.0.19045-SP0                    │    │
│  │ Kernel: 10  │  Uptime: 20d 15h 32m               │    │
│  │ NVIDIA Driver: 571.96                             │    │
│  │ Docker: 24.0.7                                    │    │
│  └─────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│  Docker Containers (3)                                  │
│  ┌─────────────────────────────────────────────────┐    │
│  │ 🟢 ollama      │ ollama/ollama:latest  │ 0 restarts│   │
│  │ 🟢 open-webui  │ ghcr.io/...           │ 0 restarts│   │
│  │ 🔴 comfyui     │ ghcr.io/...           │ 2 restarts│   │
│  └─────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│  Recent Errors (5)                                      │
│  ┌─────────────────────────────────────────────────┐    │
│  │ kernel: nvidia-container-cli failed  ×3  5m ago │    │
│  │ Service: NVIDIA LocalSystem Container    ×1  8m  │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## Implementation Plan

### Files to modify:
1. `gpu_monitor/templates/dashboard/_metrics_cards.html` — Complete rewrite
2. `gpu_monitor/dashboard/views.py` — Add network metrics query, update context
3. `gpu_monitor/templates/dashboard/rig_detail.html` — Update initial load context

### Key changes:
- **CPU**: Show model, cores, load avg, utilization bar, temp
- **Memory**: Show total/used/free/cached, progress bar, swap
- **Storage**: Per-device table with device, mount, fs, capacity, usage bar, temp, smart
- **GPU**: Per-device card with model, uuid (truncated), util bar, temp, fan, vram bar, power
- **Network**: Table with interface, ipv4, speed, rx/tx, errors
- **Motherboard**: Manufacturer, model, bios
- **Software**: Hostname, os, kernel, uptime (formatted), drivers
- **Docker**: Table with name, image, status icon, restarts
- **Errors**: Source, message preview, count, last seen

### Data not in LatestSnapshot (need separate queries):
- Network metrics: query `NetworkMetric` for latest per interface
- Motherboard: from `MetricSnapshot.motherboard_json`
- Software: from `MetricSnapshot.software_json`
- CPU load_avg: from `MetricSnapshot` (not stored — need to add or extract from metrics JSON)

### Notes:
- All sections use the same 30s HTMX polling via `_metrics_cards.html`
- Color thresholds: CPU/GPU temp (green <70, yellow 70-80, red >80), usage bars
- Uptime formatted from seconds to "Xd Xh Xm"
- GPU UUID truncated to first 12 chars for display
- Network RX/TX shown as total bytes (human-readable)
