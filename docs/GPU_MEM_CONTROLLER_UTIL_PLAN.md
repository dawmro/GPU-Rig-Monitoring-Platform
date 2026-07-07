# GPU Memory Controller Utilization - Implementation Plan

## Overview

Add GPU memory controller utilization tracking using `nvmlDeviceGetUtilizationRates(handle).memory` from pynvml. This is different from existing `mem_util_pct` which tracks memory capacity utilization (used/total).

## Key Distinction

| Metric | Source | Meaning |
|--------|--------|---------|
| `mem_util_pct` (EXISTING) | `info.used / info.total * 100` | Memory capacity utilization - how full VRAM is |
| `mem_controller_util_pct` (NEW) | `nvmlDeviceGetUtilizationRates(handle).memory` | Memory controller utilization - how busy the memory bus is |

**Example:** GPU can have 90% VRAM full (mem_util_pct=90) but only 10% memory controller activity (mem_controller_util_pct=10) if workloads are memory-capacity-bound but not bandwidth-bound.

## Architecture Flow

```
Agent (pynvml) → Ingest/Serializer → MetricSnapshot → LatestSnapshot → Charts/Dashboard
```

## Implementation Steps

### 1. Agent (Linux) - `agent/run.py`

**File:** `agent/run.py`, function `collect_gpus()` around line 618

**Current code (line 618-671):**
```python
util = pynvml.nvmlDeviceGetUtilizationRates(handle)
# ...
'gpu_util_pct': util.gpu,
# mem_util_pct calculated as capacity utilization
'mem_util_pct': round(info.used / info.total * 100, 1) if info.total else None,
```

**New code:**
```python
util = pynvml.nvmlDeviceGetUtilizationRates(handle)
# ...
'gpu_util_pct': util.gpu,
'mem_controller_util_pct': util.memory,  # NEW: Memory controller utilization %
'mem_util_pct': round(info.used / info.total * 100, 1) if info.total else None,  # Keep existing
```

### 2. Agent (Windows) - `agent_windows/run.py`

**File:** `agent_windows/run.py`, function `collect_gpus()` around line 816

**Current code (line 816-869):**
```python
util = pynvml.nvmlDeviceGetUtilizationRates(handle)
# ...
'gpu_util_pct': util.gpu,
'mem_util_pct': round(info.used / info.total * 100, 1) if info.total else None,
```

**New code:**
```python
util = pynvml.nvmlDeviceGetUtilizationRates(handle)
# ...
'gpu_util_pct': util.gpu,
'mem_controller_util_pct': util.memory,  # NEW
'mem_util_pct': round(info.used / info.total * 100, 1) if info.total else None,
```

### 3. Serializer - `gpu_monitor/metrics_app/serializers.py`

**File:** `gpu_monitor/metrics_app/serializers.py`

**Step 3a: Extract new field (around line 135, 151, 157)**
```python
# Line 135 - Add to list initialization
gpu_mem_controller_utils = []

# Line 151 - Extract in GPUMetric creation
'mem_controller_util_pct': gpu.get('mem_controller_util_pct'),  # NEW

# Line 157 - Keep existing
'mem_util_pct': gpu.get('mem_util_pct'),

# Line 172-178 - Build summary arrays for LatestSnapshot
gpu_utils.append(gpu.get('gpu_util_pct'))
gpu_mem_controller_utils.append(gpu.get('mem_controller_util_pct'))  # NEW
gpu_mem_util_pcts.append(gpu.get('mem_util_pct'))
```

**Step 3b: Store in LatestSnapshot (around line 450)**
```python
'gpu_mem_controller_utils_json': gpu_mem_controller_utils,  # NEW
'gpu_mem_util_pcts_json': gpu_mem_util_pcts,
```

### 4. Database Model - `gpu_monitor/metrics_app/models.py`

**File:** `gpu_monitor/metrics_app/models.py`, class `GPUMetric` (around line 57)

```python
class GPUMetric(models.Model):
    # ... existing fields ...
    gpu_util_pct = models.FloatField(null=True)
    mem_controller_util_pct = models.FloatField(null=True)  # NEW
    gpu_temp_c = models.FloatField(null=True)
    # ...
    mem_util_pct = models.FloatField(null=True)  # Keep existing
```

**Run migration:**
```bash
./manage.py makemigrations metrics_app
./manage.py migrate
```

**LatestSnapshot model (around line 199):**
```python
class LatestSnapshot(models.Model):
    # ... existing fields ...
    gpu_utils_json = models.JSONField(default=list, blank=True)         # [98.0, 100.0]
    gpu_mem_controller_utils_json = models.JSONField(default=list, blank=True)  # NEW
    gpu_mem_util_pcts_json = models.JSONField(default=list, blank=True)  # [66.7, 66.7]
```

### 5. Charts - Update Ingest Logic - `gpu_monitor/metrics_app/serializers.py`

**Update LatestSnapshot creation (around line 527):**
```python
ls_defaults = {
    # ... existing ...
    'gpu_utils_json': gpu_utils,
    'gpu_mem_controller_utils_json': gpu_mem_controller_utils,  # NEW
    'gpu_mem_util_pcts_json': gpu_mem_util_pcts,
    # ...
}
LatestSnapshot.objects.update_or_create(
    rig_uuid=rig_uuid,
    defaults=ls_defaults,
)
```

### 6. Charts - `gpu_monitor/dashboard/views.py`

**File:** `gpu_monitor/dashboard/views.py`

**Add new metric to chart endpoints (around line 538):**
```python
for metric in (
    'cpu_utilization_pct', 'cpu_temp_c', 'cpu_power_w',
    'total_system_power_w', 'cpu_freq_current_mhz',
    'gpu_temp_c', 'gpu_util_pct', 'gpu_power_w',
    'gpu_fan_pct', 'gpu_core_clock_mhz', 'gpu_mem_clock_mhz',
    'gpu_mem_used_mb', 'disk_usage_pct',
    'disk_read_bytes_delta', 'disk_write_bytes_delta',
    'error_frequency', 'uptime_s', 'net_rx_bytes_delta',
    'net_tx_bytes_delta', 'net_rx_errors', 'net_tx_errors',
    'gpu_mem_controller_util_pct',  # NEW
):
    for hours in (24, 168, 720):
        bucket = 1 if hours <= 24 else 60
        cache.delete(f'chart_{rig_uuid}_{metric}_{hours}_{bucket}')
```

**Add chart data endpoint handling:**
The chart endpoint at `/api/v1/rigs/<uuid>/chart-data/` should handle `metric=gpu_mem_controller_util_pct`.

### 7. Dashboard Template - `gpu_monitor/templates/dashboard/rig_detail.html`

**Add memory controller utilization chart (following existing pattern):**

In chartLoaders array (around line 540):
```javascript
{
    metric: 'gpu_mem_controller_util_pct',
    title: 'GPU Memory Controller Utilization',
    yLabel: 'Utilization (%)',
    color: '#f59e0b',  // amber
    elementId: 'chart-gpu-mem-controller-util',
    min: 0,
    max: 100
},
```

**Add chart element in Charts tab HTML:**
```html
<div class="chart-container">
    <canvas id="chart-gpu-mem-controller-util"></canvas>
</div>
```

### 8. Fleet Overview - `gpu_monitor/templates/dashboard/_rig_table.html`

**Add column for memory controller utilization:**
```html
<th class="text-left px-2 py-2 font-medium">Mem Ctrl Util [%]</th>
```

```html
<td class="px-2 py-2 whitespace-nowrap">
    {% if item.snapshot.gpu_mem_controller_utils_json %}
        <span title="{% for util in item.snapshot.gpu_mem_controller_utils_json %}GPU{{ forloop.counter }}: {% if util != None %}{{ util|floatformat:1 }}%{% else %}N/A{% endif %}{% if not forloop.last %} | {% endif %}{% endfor %}">
            {% for util in item.snapshot.gpu_mem_controller_utils_json %}
                {% if util != None %}
                <span class="{% if util >= 90 %}text-red-400{% elif util >= 70 %}text-yellow-400{% elif util >= 40 %}text-green-400{% else %}text-gray-500{% endif %}">{{ util|floatformat:1 }}%</span>
                {% else %}—{% endif %}
                {% if not forloop.last %} {% endif %}
            {% endfor %}
        </span>
    {% else %}—{% endif %}
</td>
```

## pynvml API Reference

```python
# Returns c_nvmlUtilization_t with fields:
#   gpu: unsigned int - GPU utilization percent (SM activity)
#   memory: unsigned int - Memory controller utilization percent
util = pynvml.nvmlDeviceGetUtilizationRates(handle)
util.gpu      # GPU core utilization %
util.memory   # Memory controller utilization %
```

**Note:** `util.memory` reports percentage of time memory controller was busy, NOT VRAM capacity usage.

## Testing Checklist

- [ ] Linux agent collects `mem_controller_util_pct`
- [ ] Windows agent collects `mem_controller_util_pct`
- [ ] Serializer stores in MetricSnapshot and LatestSnapshot
- [ ] Migration runs without errors
- [ ] Chart endpoint returns data for `gpu_mem_controller_util_pct`
- [ ] Chart renders in rig detail page
- [ ] Fleet overview shows Mem Ctrl Util column
- [ ] Both Linux and Windows agents work

## Files to Modify

| File | Changes |
|------|---------|
| `agent/run.py` | Add `mem_controller_util_pct` to GPU dict |
| `agent_windows/run.py` | Add `mem_controller_util_pct` to GPU dict |
| `gpu_monitor/metrics_app/models.py` | Add `mem_controller_util_pct` to GPUMetric, LatestSnapshot |
| `gpu_monitor/metrics_app/serializers.py` | Extract, store, and serialize new field |
| `gpu_monitor/metrics_app/migrations/` | New migration for model changes |
| `gpu_monitor/dashboard/views.py` | Add to chart cache invalidation |
| `gpu_monitor/templates/dashboard/rig_detail.html` | Add chart element |
| `gpu_monitor/templates/dashboard/_rig_table.html` | Add fleet overview column |

## Rollout Strategy

1. Deploy agent changes first (backward compatible - new field optional)
2. Deploy server changes (model, serializer, views)
3. Run migration
4. Verify data flows end-to-end