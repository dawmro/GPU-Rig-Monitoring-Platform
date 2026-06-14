# Database Model & Data Flow Analysis

## 1. Where is CPU load %, load average, and temperature stored?

### CPU Load %
- **Field**: `MetricSnapshot.cpu_utilization_pct` (FloatField, nullable)
- **Table**: `metrics_metricsnapshot` (timeseries, one row per rig per minute)
- **Serializer**: Line 71: `'cpu_utilization_pct': cpu.get('utilization_pct')`
- **Chart**: `ChartDataView` with `metric='cpu_utilization_pct'` — SQL `AVG` over time buckets
- **Live Metrics**: `LatestSnapshot.cpu_utilization_pct` (latest value only)

### CPU Load Average
- **Field**: `MetricSnapshot.cpu_load_avg_json` (JSONField, default=[])
- **Table**: `metrics_metricsnapshot` (timeseries)
- **Serializer**: Line 75: `'cpu_load_avg_json': cpu.get('load_avg', [])`
- **Chart**: `ChartDataView` with `metric='cpu_load_avg'` — extracts 1m/5m/15m from JSON array in Python
- **Source payload**: `metrics.cpu.load_avg` = `[1.17, 0.74, 0.63]`
- **Note**: NOT stored in LatestSnapshot — only in timeseries

### CPU Temperature
- **Field**: `MetricSnapshot.cpu_temp_c` (FloatField, nullable)
- **Table**: `metrics_metricsnapshot` (timeseries)
- **Serializer**: Line 72: `'cpu_temp_c': cpu.get('temp_c')`
- **Chart**: `ChartDataView` with `metric='cpu_temp_c'`
- **Live Metrics**: `LatestSnapshot.cpu_temp_c` (latest value only)
- **Note**: Currently null on this system (no CPU temp sensor)

---

## 2. Static vs Timeseries Data Separation

### Current Architecture: MIXED — static data duplicated in every timeseries row

**MetricSnapshot (timeseries) stores BOTH dynamic and static fields:**

| Field | Agent payload path | Changes? | Plotted? | Wasted? |
|-------|-------------------|----------|----------|---------|
| `cpu_model` | `metrics.cpu.model` | ❌ Static | ❌ No | ✅ Yes |
| `cpu_physical_cores` | `metrics.cpu.physical_cores` | ❌ Static | ❌ No | ✅ Yes |
| `cpu_logical_cores` | `metrics.cpu.logical_cores` | ❌ Static | ❌ No | ✅ Yes |
| `mem_total_bytes` | `metrics.memory.total_bytes` | ❌ Static | ❌ No | ✅ Yes |
| `swap_total_bytes` | `metrics.memory.swap_total_bytes` | ❌ Static | ❌ No | ✅ Yes |
| `motherboard_json` | `motherboard` | ❌ Static | ❌ No (displayed as text in Live Metrics) | ⚠️ Minimal |
| `software_json` | `software` | ⚠️ Semi-static (uptime changes) | ⚠️ `uptime_s` used for chart | ⚠️ Partial |
| `cpu_utilization_pct` | `metrics.cpu.utilization_pct` | ✅ Dynamic | ✅ Yes | ❌ No |
| `cpu_temp_c` | `metrics.cpu.temp_c` | ✅ Dynamic | ✅ Yes | ❌ No |
| `cpu_load_avg_json` | `metrics.cpu.load_avg` | ✅ Dynamic | ✅ Yes (load chart) | ❌ No |
| `mem_used_bytes` | `metrics.memory.used_bytes` | ✅ Dynamic | ✅ Yes | ❌ No |
| `mem_free_bytes` | `metrics.memory.free_bytes` | ✅ Dynamic | ✅ Yes | ❌ No |
| `mem_cached_bytes` | `metrics.memory.cached_bytes` | ✅ Dynamic | ✅ Yes | ❌ No |
| `swap_used_bytes` | `metrics.memory.swap_used_bytes` | ✅ Dynamic | ✅ Yes | ❌ No |
| `error_count` | `errors` (count) | ✅ Dynamic | ✅ Yes | ❌ No |

**GPUMetric (timeseries per GPU) — same problem:**

| Field | Agent payload path | Changes? | Plotted? | Wasted? |
|-------|-------------------|----------|----------|---------|
| `gpu_uuid` | `gpus[].uuid` | ❌ Static | ❌ No | ✅ Yes |
| `model` | `gpus[].model` | ❌ Static | ❌ No | ✅ Yes |
| `mem_total_mb` | `gpus[].mem_total_mb` | ❌ Static | ✅ Yes (GPU mem chart) | ❌ No |
| `pcie_max_gen` | `gpus[].pcie_max_gen` | ❌ Static | ❌ No | ✅ Yes |
| `pcie_max_width` | `gpus[].pcie_max_width` | ❌ Static | ❌ No | ✅ Yes |
| `gpu_util_pct` | `gpus[].gpu_util_pct` | ✅ Dynamic | ✅ Yes | ❌ No |
| `gpu_temp_c` | `gpus[].temp_c` | ✅ Dynamic | ✅ Yes | ❌ No |
| `fan_speed_pct` | `gpus[].fan_speed_pct` | ✅ Dynamic | ✅ Yes | ❌ No |
| `mem_used_mb` | `gpus[].mem_used_mb` | ✅ Dynamic | ✅ Yes | ❌ No |
| `mem_free_mb` | `gpus[].mem_free_mb` | ✅ Dynamic | ✅ Yes | ❌ No |
| `mem_util_pct` | `gpus[].mem_util_pct` | ✅ Dynamic | ✅ Yes | ❌ No |
| `power_draw_w` | `gpus[].power_draw_w` | ✅ Dynamic | ✅ Yes | ❌ No |
| `power_limit_w` | `gpus[].power_limit_w` | ❌ Static | ✅ Yes | ⚠️ Borderline |
| `pcie_current_gen` | `gpus[].pcie_current_gen` | ⚠️ Rarely | ✅ Yes | ⚠️ Borderline |
| `pcie_current_width` | `gpus[].pcie_current_width` | ⚠️ Rarely | ✅ Yes | ⚠️ Borderline |
| `gpu_core_clock_mhz` | `gpus[].gpu_core_clock_mhz` | ✅ Dynamic | ✅ Yes | ❌ No |
| `gpu_mem_clock_mhz` | `gpus[].gpu_mem_clock_mhz` | ✅ Dynamic | ✅ Yes | ❌ No |

**StorageMetric (timeseries per disk):**

| Field | Agent payload path | Changes? | Plotted? | Wasted? |
|-------|-------------------|----------|----------|---------|
| `device` | `storage[].device` | ❌ Static | ❌ No | ✅ Yes |
| `mountpoint` | `storage[].mountpoint` | ❌ Static | ❌ No | ✅ Yes |
| `fstype` | `storage[].fstype` | ❌ Static | ❌ No | ✅ Yes |
| `capacity_bytes` | `storage[].capacity_bytes` | ❌ Static | ❌ No | ✅ Yes |
| `usage_pct` | `storage[].usage_pct` | ✅ Dynamic | ✅ Yes | ❌ No |
| `temp_c` | `storage[].temp_c` | ✅ Dynamic | ❌ No (not plotted) | ⚠️ Borderline |
| `smart_health` | `storage[].smart_health` | ⚠️ Rarely | ❌ No (not plotted) | ⚠️ Borderline |

**NetworkMetric (timeseries per interface):**

| Field | Agent payload path | Changes? | Plotted? | Wasted? |
|-------|-------------------|----------|----------|---------|
| `interface` | `network[].interface` | ❌ Static | ❌ No | ✅ Yes |
| `ipv4` | `network[].ipv4` | ⚠️ Rarely | ❌ No | ⚠️ Borderline |
| `link_speed_mbps` | `network[].link_speed_mbps` | ❌ Static | ❌ No | ✅ Yes |
| `rx_bytes` | `network[].rx_bytes` | ✅ Dynamic | ❌ No | ⚠️ Borderline |
| `tx_bytes` | `network[].tx_bytes` | ✅ Dynamic | ❌ No | ⚠️ Borderline |
| `rx_bytes_delta` | (calculated) | ✅ Dynamic | ✅ Yes | ❌ No |
| `tx_bytes_delta` | (calculated) | ✅ Dynamic | ✅ Yes | ❌ No |
| `rx_errors` | `network[].rx_errors` | ✅ Dynamic | ✅ Yes | ❌ No |
| `tx_errors` | `network[].tx_errors` | ✅ Dynamic | ✅ Yes | ❌ No |

### Storage Waste Estimate

Per rig, per day (1,440 snapshots):

| Table | Row size | Static waste/row | Rows/day | Static waste/day |
|-------|----------|-----------------|----------|------------------|
| MetricSnapshot | ~2,943 B | ~1,200 B | 1,440 | **1.7 MB** |
| GPUMetric | ~1,447 B | ~400 B | 1,440 × 5.3 GPUs | **3.1 MB** |
| StorageMetric | ~866 B | ~300 B | 1,440 × 2.3 disks | **1.0 MB** |
| NetworkMetric | ~1,104 B | ~200 B | 1,440 × 0.9 ifaces | **0.3 MB** |
| **Total** | | | | **6.1 MB/day** |

Total timeseries storage: ~19.4 MB/day/rig
Static waste: ~6.1 MB/day/rig (**31% wasted**)

At 100 rigs × 30 days: **18.3 GB wasted** on static data duplication.

### What IS properly separated

**LatestSnapshot** — denormalized latest values, ONE row per rig:
- Updated every heartbeat
- Used for Fleet Overview and Live Metrics display
- JSON arrays for GPU/storage/network/docker data
- NO historical data — only the latest snapshot

### Fields stored but NEVER plotted on any chart:

| Field | Table | Reason |
|-------|-------|--------|
| `cpu_model` | MetricSnapshot | Static identifier |
| `cpu_physical_cores` | MetricSnapshot | Static |
| `cpu_logical_cores` | MetricSnapshot | Static |
| `mem_total_bytes` | MetricSnapshot | Static |
| `swap_total_bytes` | MetricSnapshot | Static |
| `gpu_uuid` | GPUMetric | Static identifier |
| `model` | GPUMetric | Static |
| `pcie_max_gen` | GPUMetric | Static capability |
| `pcie_max_width` | GPUMetric | Static capability |
| `device` | StorageMetric | Static identifier |
| `mountpoint` | StorageMetric | Static |
| `fstype` | StorageMetric | Static |
| `capacity_bytes` | StorageMetric | Static |
| `interface` | NetworkMetric | Static identifier |
| `link_speed_mbps` | NetworkMetric | Static |
| `schema_version` | MetricSnapshot | Metadata |
| `agent_version` | MetricSnapshot | Metadata |

---

## 3. Proposed Architecture: RigProfile for Static Data

### New table: `rigs_rigprofile`

One row per rig, stores ALL static/semi-static data:

```python
class RigProfile(models.Model):
    rig_uuid = models.UUIDField(primary_key=True)
    
    # CPU static
    cpu_model = models.CharField(max_length=255, blank=True, default='')
    cpu_physical_cores = models.PositiveIntegerField(null=True)
    cpu_logical_cores = models.PositiveIntegerField(null=True)
    
    # Memory static
    mem_total_bytes = models.BigIntegerField(null=True)
    swap_total_bytes = models.BigIntegerField(null=True)
    
    # Motherboard static
    motherboard_json = models.JSONField(default=dict, blank=True)
    
    # Software semi-static
    hostname = models.CharField(max_length=255, blank=True, default='')
    os_distro = models.CharField(max_length=255, blank=True, default='')
    kernel = models.CharField(max_length=255, blank=True, default='')
    nvidia_driver = models.CharField(max_length=64, blank=True, default='')
    docker_version = models.CharField(max_length=64, blank=True, default='')
    
    # GPU static (JSON array, one entry per GPU)
    gpu_count = models.PositiveSmallIntegerField(default=0)
    gpu_profiles_json = models.JSONField(default=list, blank=True)
    # Each entry: {uuid, model, mem_total_mb, pcie_max_gen, pcie_max_width, power_limit_w}
    
    # Storage static (JSON array, one entry per disk)
    storage_count = models.PositiveSmallIntegerField(default=0)
    storage_profiles_json = models.JSONField(default=list, blank=True)
    # Each entry: {device, mountpoint, fstype, capacity_bytes}
    
    # Network static (JSON array, one entry per interface)
    network_count = models.PositiveSmallIntegerField(default=0)
    network_profiles_json = models.JSONField(default=list, blank=True)
    # Each entry: {interface, ipv4, link_speed_mbps}
    
    updated_at = models.DateTimeField(auto_now=True)
```

### Lean timeseries tables (after refactoring):

**MetricSnapshot** — only dynamic fields:
- `id`, `rig_uuid`, `schema_version`, `agent_version`, `timestamp`
- `cpu_utilization_pct`, `cpu_temp_c`, `cpu_load_avg_json`
- `mem_used_bytes`, `mem_free_bytes`, `mem_cached_bytes`, `swap_used_bytes`
- `status`, `error_count`
- **Removed**: `cpu_model`, `cpu_physical_cores`, `cpu_logical_cores`, `mem_total_bytes`, `swap_total_bytes`, `motherboard_json`, `software_json`
- **New**: `profile_updated_at` (timestamp of last profile change, for cache invalidation)

**GPUMetric** — only dynamic fields:
- `id`, `snapshot_id`, `rig_uuid`, `timestamp`, `gpu_index`
- `gpu_util_pct`, `gpu_temp_c`, `fan_speed_pct`
- `mem_used_mb`, `mem_free_mb`, `mem_util_pct`
- `power_draw_w`, `pcie_current_gen`, `pcie_current_width`
- `gpu_core_clock_mhz`, `gpu_mem_clock_mhz`
- **Removed**: `gpu_uuid`, `model`, `mem_total_mb`, `pcie_max_gen`, `pcie_max_width`, `power_limit_w`

**StorageMetric** — only dynamic fields:
- `id`, `snapshot_id`, `rig_uuid`, `timestamp`, `device`
- `usage_pct`, `temp_c`, `smart_health`
- **Removed**: `mountpoint`, `fstype`, `capacity_bytes`

**NetworkMetric** — only dynamic fields:
- `id`, `snapshot_id`, `rig_uuid`, `timestamp`, `interface`
- `rx_bytes_delta`, `tx_bytes_delta`, `rx_errors`, `tx_errors`
- **Removed**: `ipv4`, `link_speed_mbps`, `rx_bytes`, `tx_bytes`

### Data flow after refactoring:

```
Agent payload
    │
    ├── Static data (cpu_model, gpu model, etc.)
    │       └── Upsert into RigProfile (one row per rig)
    │
    └── Dynamic data (cpu_util, gpu_temp, etc.)
            └── Insert into lean timeseries tables
                    └── Foreign key to RigProfile for static lookup
```

### Storage savings:

| Table | Current row size | New row size | Savings |
|-------|-----------------|--------------|---------|
| MetricSnapshot | ~2,943 B | ~1,700 B | **42%** |
| GPUMetric | ~1,447 B | ~1,000 B | **31%** |
| StorageMetric | ~866 B | ~550 B | **36%** |
| NetworkMetric | ~1,104 B | ~900 B | **18%** |
| RigProfile (new) | — | ~2,000 B | One-time per rig |

**Net savings: ~30-35% reduction in total timeseries storage**

At 100 rigs × 30 days: **~11 GB saved** (from ~36 GB to ~25 GB)
