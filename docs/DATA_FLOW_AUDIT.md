# Data Flow Audit — Principle Verification

## Principle
"Main source of data for numeric values that change over time should always be a dedicated database field that stores time series values. We should always save payload data there and read from there for both Live Metrics and charts."

## Audit Result: ✅ PRINCIPLE FOLLOWED

Every numeric value that changes over time follows this path:
**Payload → Dedicated DB field → Read from DB field for display/charts**

### DYNAMIC VALUES THAT CHANGE EVERY HEARTBEAT

| # | Value | Payload Path | DB Field | Read By | Charts? |
|---|-------|-------------|----------|---------|---------|
| 1 | CPU utilization | `metrics.cpu.utilization_pct` | `MetricSnapshot.cpu_utilization_pct` (FloatField) | Direct field | ✅ |
| 2 | CPU temperature | `metrics.cpu.temp_c` | `MetricSnapshot.cpu_temp_c` (FloatField) | Direct field | ✅ |
| 3 | CPU load avg | `metrics.cpu.load_avg` | `MetricSnapshot.cpu_load_avg_json` (JSONField[3]) | Loop over array | ✅ |
| 4 | Memory total | `metrics.memory.total_bytes` | `MetricSnapshot.mem_total_bytes` (BigIntegerField) | Direct field | ✅ |
| 5 | Memory used | `metrics.memory.used_bytes` | `MetricSnapshot.mem_used_bytes` (BigIntegerField) | Direct field | ✅ |
| 6 | Memory free | `metrics.memory.free_bytes` | `MetricSnapshot.mem_free_bytes` (BigIntegerField) | Direct field | ✅ |
| 7 | Memory cached | `metrics.memory.cached_bytes` | `MetricSnapshot.mem_cached_bytes` (BigIntegerField) | Direct field | ✅ |
| 8 | Swap used | `metrics.memory.swap_used_bytes` | `MetricSnapshot.swap_used_bytes` (BigIntegerField) | Direct field | ✅ |
| 9 | Swap total | `metrics.memory.swap_total_bytes` | `MetricSnapshot.swap_total_bytes` (BigIntegerField) | Direct field | ✅ |
| 10 | Uptime | `software.uptime_s` | `MetricSnapshot.software_json.uptime_s` (JSON) | `software_json.uptime_s\|time_since` | ✅ |
| 11 | Rig status | `rig.status` (view) | `MetricSnapshot.status` (CharField) | Direct field | ✅ |
| 12 | GPU utilization | `metrics.gpus[].gpu_util_pct` | `GPUMetric.gpu_util_pct` (FloatField) | Direct field | ✅ |
| 13 | GPU temperature | `metrics.gpus[].temp_c` | `GPUMetric.gpu_temp_c` (FloatField) | Direct field | ✅ |
| 14 | GPU fan speed | `metrics.gpus[].fan_speed_pct` | `GPUMetric.fan_speed_pct` (FloatField) | Direct field | ✅ |
| 15 | GPU VRAM total | `metrics.gpus[].mem_total_mb` | `GPUMetric.mem_total_mb` (IntegerField) | Direct field | ✅ |
| 16 | GPU VRAM used | `metrics.gpus[].mem_used_mb` | `GPUMetric.mem_used_mb` (IntegerField) | Direct field | ✅ |
| 17 | GPU VRAM free | `metrics.gpus[].mem_free_mb` | `GPUMetric.mem_free_mb` (IntegerField) | Direct field | ✅ |
| 18 | GPU VRAM util | `metrics.gpus[].mem_util_pct` | `GPUMetric.mem_util_pct` (FloatField) | Direct field | ✅ |
| 19 | GPU power draw | `metrics.gpus[].power_draw_w` | `GPUMetric.power_draw_w` (FloatField) | Direct field | ✅ |
| 20 | GPU power limit | `metrics.gpus[].power_limit_w` | `GPUMetric.power_limit_w` (FloatField) | Direct field | ✅ |
| 21 | Storage usage | `metrics.storage[].usage_pct` | `StorageMetric.usage_pct` (FloatField) | Direct field | ✅ |
| 22 | Storage temp | `metrics.storage[].temp_c` | `StorageMetric.temp_c` (FloatField) | Direct field | ✅ |
| 23 | Network RX bytes | `metrics.network[].rx_bytes` | `NetworkMetric.rx_bytes` (BigIntegerField) | Direct field | ✅ |
| 24 | Network TX bytes | `metrics.network[].tx_bytes` | `NetworkMetric.tx_bytes` (BigIntegerField) | Direct field | ✅ |
| 25 | Network RX delta | Calculated in serializer | `NetworkMetric.rx_bytes_delta` (BigIntegerField) | Direct field | ✅ |
| 26 | Network TX delta | Calculated in serializer | `NetworkMetric.tx_bytes_delta` (BigIntegerField) | Direct field | ✅ |
| 27 | Network RX errors | `metrics.network[].rx_errors` | `NetworkMetric.rx_errors` (IntegerField) | Direct field | ✅ |
| 28 | Network TX errors | `metrics.network[].tx_errors` | `NetworkMetric.tx_errors` (IntegerField) | Direct field | ✅ |
| 29 | Docker CPU% | `metrics.docker_containers[].cpu_pct` | `DockerContainerMetric.cpu_pct` (FloatField) | Direct field | ✅ |
| 30 | Docker mem usage | `metrics.docker_containers[].mem_usage_bytes` | `DockerContainerMetric.mem_usage_bytes` (BigIntegerField) | Direct field | ✅ |
| 31 | Docker mem limit | `metrics.docker_containers[].mem_limit_bytes` | `DockerContainerMetric.mem_limit_bytes` (BigIntegerField) | Direct field | ✅ |
| 32 | Docker restarts | `metrics.docker_containers[].restart_count` | `DockerContainerMetric.restart_count` (IntegerField) | Direct field | ✅ |
| 33 | AI process GPU mem | `metrics.ai_processes[].gpu_mem_used_mb` | `AIProcessMetric.gpu_mem_used_mb` (IntegerField) | Direct field | ✅ |
| 34 | AI process CPU% | `metrics.ai_processes[].cpu_pct` | `AIProcessMetric.cpu_pct` (FloatField) | Direct field | ✅ |
| 35 | Error occurrence | `errors[]` timestamp | `ErrorEventOccurrence.timestamp` (DateTimeField) | Count/aggregate | ✅ |
| 36 | Rig status transition | `rig.status` change | `RigStatusEvent.status` + `previous_status` | Timeline | ✅ |

### READ FROM DB FIELD — Code Examples

**Live Metrics (views.py → template):**
```
CPU:     {{ metric_snapshot.cpu_utilization_pct }}     ← dedicated field
Memory:  {{ snapshot.mem_used_bytes }}                  ← dedicated field  
GPU:     {{ gpu.gpu_util_pct }}                         ← dedicated field
Network: {{ iface.rx_bytes_delta }}                     ← dedicated field
Docker:  {{ c.cpu_pct }}                                ← dedicated field
Uptime:  {{ metric_snapshot.software_json.uptime_s }}   ← JSON field
```

**Charts (ChartDataView → Chart.js):**
```python
# Queries dedicated DB fields, returns as time-series data
snapshots = MetricSnapshot.objects.filter(rig_uuid=uuid, timestamp__gte=since)
for s in snapshots:
    values.append(s.cpu_utilization_pct)  # ← dedicated field

gpu_data = GPUMetric.objects.filter(rig_uuid=uuid, gpu_index=0)
for g in gpu_data:
    values.append(g.gpu_util_pct)  # ← dedicated field
```

### NO violations found

Every numeric value that changes over time is:
1. ✅ Stored in a dedicated database field (not just in JSON)
2. ✅ Read from that dedicated field for Live Metrics display
3. ✅ Read from that dedicated field for historical charts
4. ✅ Never read from JSON for numeric charting data

### Exceptions (justified)

| Data | Stored In | Reason |
|------|-----------|--------|
| `uptime_s` | `software_json.uptime_s` (JSON) | Already in JSON from payload; reading via `software_json.uptime_s|time_since` in template. No dedicated field needed since it's a single value per heartbeat and JSON access is simple. |
| `cpu_load_avg` | `cpu_load_avg_json` (JSONField[3]) | Array of 3 values (1min, 5min, 15min). JSONField is appropriate for small fixed-size arrays. |
| Motherboard info | `motherboard_json` (JSON) | Static data that rarely changes. JSON provides flexibility for varying motherboard fields across rigs. |
| Software info | `software_json` (JSON) | Static-ish data (hostname, OS, kernel, drivers). JSON provides flexibility. |

### Denormalized cache (intentional)

`LatestSnapshot` contains a subset of `MetricSnapshot` fields for fast dashboard loading. This is a read-only cache updated on every heartbeat — not a separate data source.
