# GPU Memory Controller Utilization - Implementation Plan

## Overview

Add GPU memory controller utilization tracking using `nvmlDeviceGetUtilizationRates(handle).memory` from pynvml. This is **different** from existing `mem_util_pct` which tracks VRAM capacity utilization (used/total).

## Key Distinction

| Metric | Source | Meaning |
|--------|--------|---------|
| `mem_util_pct` (EXISTING) | `info.used / info.total * 100` | VRAM capacity utilization - how full VRAM is |
| `mem_controller_util_pct` (NEW) | `nvmlDeviceGetUtilizationRates(handle).memory` | Memory controller/bus utilization - how busy the memory bus is |

**Example:** GPU can have 90% VRAM full (`mem_util_pct=90`) but only 10% memory controller activity (`mem_controller_util_pct=10`) if workloads are capacity-bound not bandwidth-bound.

---

## Architecture Flow

```
Agent (pynvml) 
    → Ingest/Serializer 
        → MetricSnapshot/GPUMetric (timeseries)
        → LatestSnapshot (current state)
            → Charts/Dashboard
```

---

## Implementation Steps

### 1. Agent (Linux) - `agent/run.py`

**File:** `agent/run.py`, function `collect_gpus()` around line 618

**Current code (line 618-671):**
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
'mem_controller_util_pct': util.memory,  # NEW: Memory controller utilization %
'mem_util_pct': round(info.used / info.total * 100, 1) if info.total else None,
```

---

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

---

### 3. Database Model - `gpu_monitor/metrics_app/models.py`

**File:** `gpu_monitor/metrics_app/models.py`, class `GPUMetric` (line 57)

**Add new field:**
```python
class GPUMetric(models.Model):
    # ... existing fields ...
    gpu_util_pct = models.FloatField(null=True)
    mem_controller_util_pct = models.FloatField(null=True)  # NEW
    gpu_temp_c = models.FloatField(null=True)
    # ... existing ...
    mem_util_pct = models.FloatField(null=True)  # Keep existing
```

**LatestSnapshot model (around line 199):**
```python
class LatestSnapshot(models.Model):
    # ... existing fields ...
    gpu_utils_json = models.JSONField(default=list, blank=True)          # [98.0, 100.0]
    gpu_mem_controller_utils_json = models.JSONField(default=list, blank=True)  # NEW
    gpu_mem_util_pcts_json = models.JSONField(default=list, blank=True)  # [66.7, 66.7]
```

**Run migrations:**
```bash
./manage.py makemigrations metrics_app
./manage.py migrate
```

---

### 4. Serializer - `gpu_monitor/metrics_app/serializers.py`

**File:** `gpu_monitor/metrics_app/serializers.py`

**Step 4a: Extract new field (around line 135):**
```python
# Line 135 - Add to list initialization
gpu_mem_controller_utils = []

# Line 151 - In GPUMetric creation
'mem_controller_util_pct': gpu.get('mem_controller_util_pct'),  # NEW

# Line 157 - Keep existing
'mem_util_pct': gpu.get('mem_util_pct'),

# Lines 172-178 - Build summary arrays for LatestSnapshot
gpu_utils.append(gpu.get('gpu_util_pct'))
gpu_mem_controller_utils.append(gpu.get('mem_controller_util_pct'))  # NEW
gpu_mem_util_pcts.append(gpu.get('mem_util_pct'))
```

**Step 4b: Store in LatestSnapshot (around line 450):**
```python
'gpu_mem_controller_utils_json': gpu_mem_controller_utils,  # NEW
'gpu_mem_util_pcts_json': gpu_mem_util_pcts,
```

**Step 4c: Update LatestSnapshot creation (around line 527):**
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

---

### 5. Compaction Script - `gpu_monitor/metrics_app/management/commands/compact_data.py`

**File:** `gpu_monitor/metrics_app/management/commands/compact_data.py`

**Add to `GPUMetric` compaction config (around line 36-47):**
```python
{
    'table': 'metrics_gpumetric',
    'group_by': ['rig_uuid', 'gpu_index'],
    'agg_fields': {
        'gpu_util_pct': 'avg',
        'mem_controller_util_pct': 'avg',  # NEW
        'gpu_temp_c': 'avg',
        'fan_speed_pct': 'avg',
        'mem_used_mb': 'avg',
        'mem_free_mb': 'avg',
        'mem_total_mb': 'last',
        'mem_util_pct': 'avg',
        'mem_controller_util_pct': 'avg',  # NEW
        'power_draw_w': 'avg',
        'power_limit_w': 'last',
        'pcie_current_gen': 'last',
        'pcie_max_gen': 'last',
        'pcie_current_width': 'last',
        'pcie_max_width': 'last',
        'gpu_core_clock_mhz': 'avg',
        'gpu_mem_clock_mhz': 'avg',
    },
    'static_fields': ['model', 'snapshot_id'],
},
```

---

### 6. Charts - Dashboard Views

**File:** `gpu_monitor/dashboard/views.py` (around line 538)

**Add to chart invalidation list:**
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
```

---

### 7. Fleet Overview Table - Template

**File:** `gpu_monitor/templates/dashboard/_rig_table.html`

**Add column header (around line 9):**
```html
<th class="text-left px-2 py-2 font-medium">Mem Ctrl [%]</th>
```

**Add column cell (after GPU Util column):**
```html
<td class="px-2 py-2 whitespace-nowrap">
    {% if item.snapshot.gpu_count %}
        <span title="{% for util in item.snapshot.gpu_mem_controller_utils_json %}GPU{{ forloop.counter }}: {% if util != None %}{{ util|floatformat:1 }}%{% else %}N/A{% endif %}{% if not forloop.last %} | {% endif %}{% endfor %}">
            {% for util in item.snapshot.gpu_mem_controller_utils_json %}
                {% if util != None %}
                    <span class="{% if util >= 90 %}text-red-400{% elif util >= 70 %}text-yellow-400{% elif util >= 40 %}text-orange-400{% else %}text-green-400{% endif %}">{{ util|floatformat:1 }}%</span>
                {% else %}N/A{% endif %}
                {% if not forloop.last %} {% endif %}
            {% endfor %}
        </span>
    {% else %}—{% endif %}
</td>
```

---

## pynvml Reference

```python
# From pynvml docs:
util = pynvml.nvmlDeviceGetUtilizationRates(handle)
# Returns nvmlUtilization_t with fields:
#   gpu:    GPU utilization percentage (0-100)
#   memory: Memory controller utilization percentage (0-100)
#   encoder:  Encoder utilization percentage (0-100)
#   decoder:  Decoder utilization percentage (0-100)
```

**Usage:**
```python
util = pynvml.nvmlDeviceGetUtilizationRates(handle)
gpu_util = util.gpu        # 0-100
mem_controller_util = util.memory  # 0-100 - NEW
```

---

## Testing Checklist

- [ ] Agent collects `mem_controller_util_pct` on Linux
- [ ] Agent collects `mem_controller_util_pct` on Windows
- [ ] Serializer stores in GPUMetric.mem_controller_util_pct
- [ ] Serializer stores in LatestSnapshot.gpu_mem_controller_utils_json
- [ ] Migration runs without errors
- [ ] Compaction script aggregates mem_controller_util_pct with 'avg'
- [ ] Charts show gpu_mem_controller_util_pct metric
- [ ] Fleet overview table shows Mem Ctrl [%] column
- [ ] Data flows correctly: Agent → Ingest → DB → Charts → Dashboard

---

## Rollout Plan

1. **Create branch:** `plan/gpu-mem-util`
2. **Run migrations** on staging
3. **Deploy agents** (Linux + Windows) with new code
4. **Verify** data flows in dashboard
5. **Test compaction** with `compact_data --dry-run`
6. **Merge** to main after verification

---

## Files to Modify Summary

| File | Changes |
|------|---------|
| `agent/run.py` | Add `mem_controller_util_pct` in collect_gpus() |
| `agent_windows/run.py` | Add `mem_controller_util_pct` in collect_gpus() |
| `gpu_monitor/metrics_app/models.py` | Add `mem_controller_util_pct` to GPUMetric + LatestSnapshot |
| `gpu_monitor/metrics_app/serializers.py` | Extract, store, and forward new field |
| `gpu_monitor/metrics_app/management/commands/compact_data.py` | Add to GPUMetric compaction config |
| `gpu_monitor/dashboard/views.py` | Add to chart invalidation list |
| `gpu_monitor/templates/dashboard/_rig_table.html` | Add Mem Ctrl [%] column |

---

## Migration Notes

```bash
# Generate migration
./manage.py makemigrations metrics_app

# Check migration
./manage.py sqlmigrate metrics_app <migration_number>

# Apply
./manage.py migrate
```