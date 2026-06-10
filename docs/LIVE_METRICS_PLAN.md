# Live Metrics Detail Display — Updated Plan

## What was added/changed in this session

### New fields added to MetricSnapshot model (+migration 0006)
- `cpu_load_avg_json` — stores load average array [1min, 5min, 15min]
- `mem_free_bytes` — free memory
- `swap_used_bytes` — swap used
- `swap_total_bytes` — swap total

### Updated MetricSnapshotSerializer to store all fields from payload
- `cpu_load_avg_json`: `cpu.get('load_avg', [])`
- `mem_free_bytes`: `memory.get('free_bytes')`
- `swap_used_bytes`: `memory.get('swap_used_bytes')`
- `swap_total_bytes`: `memory.get('swap_total_bytes')`

### Updated _metrics_cards.html template
- **CPU**: Now shows load average (e.g. "0.99 / 0.99 / 0.99")
- **Memory**: Now reads from MetricSnapshot (has all fields). Shows:
  - Used, Total, Free, Cached
  - Swap used/total with percentage
- **GPU**: VRAM now shows util% and free MB
- **Storage**: Section header shows device count: "Storage (5)"
- **GPU**: Section header shows device count: "GPU (1)"
- **Network**: Section header shows interface count: "Network (4)"

### Data source mapping (important)
| Data | Source | Reason |
|------|--------|--------|
| CPU model, cores, load_avg | `MetricSnapshot` | LatestSnapshot doesn't have these fields |
| Memory total/used/free/cached/swap | `MetricSnapshot` | LatestSnapshot only has total/used |
| Motherboard, software | `MetricSnapshot` | LatestSnapshot doesn't have these |
| GPU metrics | `GPUMetric` | Per-device, queried separately |
| Storage metrics | `StorageMetric` | Per-device, queried separately |
| Network metrics | `NetworkMetric` | Per-interface, queried separately |
| Docker containers | `DockerContainerMetric` | Per-container, queried separately |
| Errors | `Rig.latest_errors_json` | Latest payload errors, updated in place (like motherboard_json) |

### Known issues fixed
1. **Storage dedup**: Normalized device paths with `.rstrip('/\\')` to handle `C:\` vs `C:\\`
2. **Cross-contamination**: Cleaned up stale test data from DB
3. **Uptime calculation**: Fixed agent bug — `psutil.boot_time()` returns epoch timestamp, not uptime
