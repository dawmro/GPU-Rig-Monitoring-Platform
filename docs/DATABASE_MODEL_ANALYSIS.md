# Database Model Analysis

## 1. Where is CPU load %, load average, and temperature stored?

### CPU Load %
- **Field**: `MetricSnapshot.cpu_utilization_pct` (FloatField, nullable)
- **Table**: `metrics_metricsnapshot` (timeseries, one row per rig per minute)
- **Chart query**: `ChartDataView` with `metric='cpu_utilization_pct'`
- **Source**: Agent sends `metrics.cpu.utilization_pct`

### CPU Load Average
- **Field**: `MetricSnapshot.cpu_load_avg_json` (JSONField, default=[])
- **Table**: `metrics_metricsnapshot` (timeseries)
- **Chart query**: `ChartDataView` with `metric='cpu_load_avg'` — special handling extracts 1m/5m/15m from JSON array
- **Source**: Agent sends `metrics.cpu.load_avg` as array `[1.5, 1.2, 0.8]`
- **Display**: Three separate lines for 1-minute, 5-minute, 15-minute load averages

### CPU Temperature
- **Field**: `MetricSnapshot.cpu_temp_c` (FloatField, nullable)
- **Table**: `metrics_metricsnapshot` (timeseries)
- **Chart query**: `ChartDataView` with `metric='cpu_temp_c'`
- **Source**: Agent sends `metrics.cpu.temp_c` (currently null on this system — no CPU temp sensor)

### Also in LatestSnapshot (denormalized, latest value only):
- `LatestSnapshot.cpu_utilization_pct` — latest CPU utilization
- `LatestSnapshot.cpu_temp_c` — latest CPU temperature
- Note: `cpu_load_avg_json` is NOT in LatestSnapshot — only in MetricSnapshot timeseries

---

## 2. Are static and timeseries data separated?

### Current Architecture: MIXED — static data duplicated in timeseries

**MetricSnapshot (timeseries) contains BOTH dynamic and static fields:**

| Field | Type | Changes? | Wasted space? |
|-------|------|----------|---------------|
| `cpu_model` | CharField(255) | Static (never changes) | ✅ Wasted — same value every minute |
| `cpu_physical_cores` | PositiveIntegerField | Static | ✅ Wasted |
| `cpu_logical_cores` | PositiveIntegerField | Static | ✅ Wasted |
| `mem_total_bytes` | BigIntegerField | Static | ✅ Wasted |
| `swap_total_bytes` | BigIntegerField | Static | ✅ Wasted |
| `motherboard_json` | JSONField | Static | ✅ Wasted — same JSON every minute |
| `software_json` | JSONField | Semi-static (kernel, hostname rarely change) | ⚠️ Partially wasted |
| `cpu_utilization_pct` | FloatField | Dynamic | ✅ Correct |
| `cpu_temp_c` | FloatField | Dynamic | ✅ Correct |
| `cpu_load_avg_json` | JSONField | Dynamic | ✅ Correct |
| `mem_used_bytes` | BigIntegerField | Dynamic | ✅ Correct |
| `mem_free_bytes` | BigIntegerField | Dynamic | ✅ Correct |
| `mem_cached_bytes` | BigIntegerField | Dynamic | ✅ Correct |
| `swap_used_bytes` | BigIntegerField | Dynamic | ✅ Correct |
| `error_count` | PositiveIntegerField | Dynamic | ✅ Correct |

**Same pattern in child tables:**

| Table | Static fields (wasted) | Dynamic fields |
|-------|----------------------|----------------|
| `GPUMetric` | `gpu_uuid`, `model`, `mem_total_mb`, `pcie_max_gen`, `pcie_max_width` | `gpu_util_pct`, `gpu_temp_c`, `fan_speed_pct`, `mem_used_mb`, `mem_free_mb`, `mem_util_pct`, `power_draw_w`, `power_limit_w`, `pcie_current_gen`, `pcie_current_width`, `gpu_core_clock_mhz`, `gpu_mem_clock_mhz` |
| `StorageMetric` | `device`, `mountpoint`, `fstype`, `capacity_bytes` | `usage_pct`, `temp_c`, `smart_health` |
| `NetworkMetric` | `interface`, `ipv4`, `link_speed_mbps` | `rx_bytes`, `tx_bytes`, `rx_bytes_delta`, `tx_bytes_delta`, `rx_errors`, `tx_errors` |

### Storage waste estimate (per rig, per day):

- `MetricSnapshot`: ~2,943 bytes/row × 1,440 rows/day = **4.2 MB/day** of which ~1,200 bytes/row is static = **1.7 MB/day wasted**
- `GPUMetric`: ~1,447 bytes/row × 1,440 rows/day × 5.3 GPUs = **11.0 MB/day** of which ~400 bytes/row is static = **3.1 MB/day wasted**
- `StorageMetric`: ~866 bytes/row × 1,440 rows/day × 2.3 disks = **2.8 MB/day** of which ~300 bytes/row is static = **1.0 MB/day wasted**
- `NetworkMetric`: ~1,104 bytes/row × 1,440 rows/day × 0.9 interfaces = **1.4 MB/day** of which ~200 bytes/row is static = **0.3 MB/day wasted**

**Total static waste: ~6.0 MB/day/rig** (out of ~19.4 MB/day/rig total)

### What IS properly separated:

**LatestSnapshot** — denormalized latest values, one row per rig:
- Stores only the LATEST value of each metric as JSON arrays
- Updated every heartbeat (no historical data)
- Used for Fleet Overview and Live Metrics display
- Fields: `cpu_utilization_pct`, `cpu_temp_c`, `mem_used_bytes`, `mem_total_bytes`, plus JSON arrays for GPU/storage/network/docker
- This is the correct pattern for "current state" display

### Recommendations:

1. **Separate static data into a `RigProfile` table** — one row per rig, stores `cpu_model`, `cpu_cores`, `mem_total_bytes`, `motherboard_json`, `software_json`, GPU models, disk devices, network interfaces
2. **Keep MetricSnapshot lean** — only store dynamic metrics that change over time
3. **Use foreign key** from MetricSnapshot to RigProfile for static data lookup
4. **Estimated savings**: ~30% reduction in MetricSnapshot storage, ~40% in GPUMetric/StorageMetric/NetworkMetric

### Charts that use timeseries data:

| Chart | Source table | Fields used |
|-------|-------------|-------------|
| CPU % | MetricSnapshot | `cpu_utilization_pct` |
| CPU Temp | MetricSnapshot | `cpu_temp_c` |
| CPU Load Avg | MetricSnapshot | `cpu_load_avg_json` (1m/5m/15m) |
| Memory | MetricSnapshot | `mem_used_bytes`, `mem_free_bytes`, `swap_used_bytes` |
| GPU Temp | GPUMetric | `gpu_temp_c` |
| GPU Util | GPUMetric | `gpu_util_pct` |
| GPU Mem | GPUMetric | `mem_used_mb`, `mem_total_mb` |
| GPU Power | GPUMetric | `power_draw_w`, `power_limit_w` |
| GPU Fan | GPUMetric | `fan_speed_pct` |
| Disk Usage | StorageMetric | `usage_pct` |
| Network | NetworkMetric | `rx_bytes_delta`, `tx_bytes_delta` |
| Error Freq | MetricSnapshot | `error_count` |
| Uptime | MetricSnapshot | `software_json.uptime_s` |

### Fields stored but NOT plotted on any chart:

| Field | Table | Wasted |
|-------|-------|--------|
| `cpu_model` | MetricSnapshot | ✅ Yes — static, not plotted |
| `cpu_physical_cores` | MetricSnapshot | ✅ Yes — static, not plotted |
| `cpu_logical_cores` | MetricSnapshot | ✅ Yes — static, not plotted |
| `mem_total_bytes` | MetricSnapshot | ✅ Yes — static, not plotted |
| `swap_total_bytes` | MetricSnapshot | ✅ Yes — static, not plotted |
| `motherboard_json` | MetricSnapshot | ✅ Yes — static, not plotted |
| `software_json` | MetricSnapshot | ⚠️ Partially — uptime_s IS used for uptime chart, but hostname/os_distro/kernel are not plotted |
| `gpu_uuid` | GPUMetric | ✅ Yes — static identifier, not plotted |
| `model` | GPUMetric | ✅ Yes — static, not plotted |
| `mem_total_mb` | GPUMetric | ⚠️ Used for GPU memory chart |
| `pcie_max_gen` | GPUMetric | ✅ Yes — static, not plotted |
| `pcie_max_width` | GPUMetric | ✅ Yes — static, not plotted |
| `device` | StorageMetric | ✅ Yes — static, not plotted |
| `mountpoint` | StorageMetric | ✅ Yes — static, not plotted |
| `fstype` | StorageMetric | ✅ Yes — static, not plotted |
| `capacity_bytes` | StorageMetric | ✅ Yes — static, not plotted |
| `interface` | NetworkMetric | ✅ Yes — static, not plotted |
| `ipv4` | NetworkMetric | ✅ Yes — static, not plotted |
| `link_speed_mbps` | NetworkMetric | ✅ Yes — static, not plotted |
| `schema_version` | MetricSnapshot | ✅ Yes — metadata, not plotted |
| `agent_version` | MetricSnapshot | ✅ Yes — metadata, not plotted |
