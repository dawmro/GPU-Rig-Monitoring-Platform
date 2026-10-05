# Implementation Plan: Per-CPU-Core Statistics Collection

**Branch:** `plan/per-cpu-core-statistics`  
**Author:** Agent  
**Date:** 2026-10-06  
**Status:** Planning Phase

---

## Executive Summary

Currently the agent collects a single aggregate `cpu_percent` value (system-wide average across all logical cores). This plan extends the collection to gather **per-logical-core utilization percentages** using `psutil.cpu_percent(interval=1, percpu=True)`, while preserving the existing average for backward compatibility.

The implementation spans:
1. **Agent** (`agent/run.py`) - Collect per-core data
2. **Schema Version** - Increment to `1.20`
3. **Server Models** (`metrics_app/models.py`) - Add JSON fields for per-core time-series
4. **Serializer** (`metrics_app/serializers.py`) - Ingest per-core data
5. **Chart View** (`metrics_app/views.py`) - New chart metrics for per-core
6. **Live Metrics** (`dashboard/views.py` + `_metrics_cards.html`) - Display per-core bars
7. **Django Checks** (`metrics_app/checks.py`) - Validate new fields

---

## Current Implementation Analysis

### Agent Side (`agent/run.py:128-210`)

```python
def collect_cpu():
    cpu_percent = psutil.cpu_percent(interval=1)  # Single aggregate value
    cpu_count_phys = psutil.cpu_count(logical=False)
    cpu_count_log = psutil.cpu_count(logical=True)
    load_avg = os.getloadavg()
    # ... temp, freq, model ...
    return {
        'model': model,
        'physical_cores': cpu_count_phys,
        'logical_cores': cpu_count_log,
        'load_avg': list(load_avg),
        'utilization_pct': cpu_percent,  # ← ONLY aggregate today
        'temp_c': temp_c,
        'freq': cpu_freq,
    }
```

**Payload structure sent to server:**
```json
{
  "metrics": {
    "cpu": {
      "model": "AMD Ryzen 9 5950X",
      "physical_cores": 16,
      "logical_cores": 32,
      "load_avg": [1.23, 1.45, 1.67],
      "utilization_pct": 42.5,
      "temp_c": 58.2,
      "freq": {"current_mhz": 3400, "min_mhz": 2200, "max_mhz": 4900}
    }
  }
}
```

### Server Models

**MetricSnapshot** (time-series, one row per minute):
- `cpu_utilization_pct` - FloatField (aggregate)
- `cpu_load_avg_json` - JSONField (1m, 5m, 15m)
- `cpu_temp_c`, `cpu_freq_*` - scalar fields

**LatestSnapshot** (denormalized latest per rig):
- `cpu_utilization_pct`, `cpu_temp_c`, `cpu_load_avg_json`
- `cpu_model`, `cpu_physical_cores`, `cpu_logical_cores`

### Chart Data View (`ChartDataView.SNAPSHOT_METRICS`)

Metrics that aggregate on-the-fly from `MetricSnapshot`:
- `cpu_utilization_pct` → AVG per bucket
- `cpu_load_avg` → special handling for 3-value JSON
- `cpu_temp_c`, `cpu_freq_*`, `cpu_power_w`, etc.

### Live Metrics Display (`_metrics_cards.html:89-157`)

Shows:
- CPU model, cores/threads
- Progress bar with aggregate `cpu_utilization_pct`
- Temperature, frequency, load average

---

## Implementation Plan

---

### Step 1: Agent Collection — Extend `collect_cpu()` 

**File:** `agent/run.py` (and `agent_windows/run.py` for parity)

**Changes:**
```python
def collect_cpu():
    # Single call with percpu=True — blocks 1s, returns per-logical-core utilization
    # This replaces the separate aggregate call; aggregate is derived from per-core
    cpu_per_core = psutil.cpu_percent(interval=1, percpu=True)
    
    # Derive system-wide aggregate from per-core values (average across all logical cores)
    # This matches the semantics of the old psutil.cpu_percent(interval=1) call
    cpu_percent = sum(cpu_per_core) / len(cpu_per_core) if cpu_per_core else 0.0
    
    cpu_count_phys = psutil.cpu_count(logical=False)
    cpu_count_log = psutil.cpu_count(logical=True)
    load_avg = os.getloadavg()

    # NEW: Per-core frequency (Linux/FreeBSD only; returns list with single element on other platforms)
    cpu_freq_per_core = []
    try:
        freq_list = psutil.cpu_freq(percpu=True)
        if freq_list:
            cpu_freq_per_core = [
                {
                    'current_mhz': round(f.current, 1) if f.current is not None else None,
                    'min_mhz': round(f.min, 1) if f.min is not None else None,
                    'max_mhz': round(f.max, 1) if f.max is not None else None,
                }
                for f in freq_list
            ]
    except (AttributeError, OSError, NotImplementedError):
        pass  # Per-core freq not supported on this platform

    # NEW: Per-core temperature (from sensors_temperatures)
    # Strategy: find coretemp/k10temp sensors, extract Core 0, Core 1, etc. temps
    cpu_temp_per_core = []
    try:
        temps = psutil.sensors_temperatures()
        # Priority order for CPU temperature sensors
        cpu_sensor_names = ('coretemp', 'k10temp')
        core_temps = {}
        for name in cpu_sensor_names:
            if name in temps:
                for entry in temps[name]:
                    # Match "Core 0", "Core 1", etc. labels
                    if entry.label and entry.label.startswith('Core '):
                        try:
                            core_idx = int(entry.label.split()[1])
                            if entry.current is not None:
                                core_temps[core_idx] = entry.current
                        except (ValueError, IndexError):
                            pass
                if core_temps:
                    break  # Found core temps in preferred sensor
        
        # Build ordered list matching logical core indices
        # If we have fewer core temps than logical cores, fill with None
        for i in range(cpu_count_log):
            cpu_temp_per_core.append(core_temps.get(i))
    except Exception:
        pass

    # Existing aggregate frequency (for backward compat + non-Linux)
    cpu_freq = None
    try:
        freq = psutil.cpu_freq(percpu=False)
        if freq is not None and freq.current is not None:
            cpu_freq = {
                'current_mhz': round(freq.current, 1),
                'min_mhz': round(freq.min, 1) if freq.min is not None else None,
                'max_mhz': round(freq.max, 1) if freq.max is not None else None,
            }
    except (AttributeError, OSError, NotImplementedError) as e:
        logging.getLogger('cpu').debug('CPU frequency unavailable: %s', e)
    except Exception as e:
        logging.getLogger('cpu').warning('CPU frequency collection failed: %s', e)

    # Existing aggregate temp (for backward compat)
    temp_c = None
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            cpu_sensor_names = ('coretemp', 'k10temp')
            best_temp = None
            for name in cpu_sensor_names:
                if name in temps:
                    for entry in temps[name]:
                        if entry.current is not None:
                            if best_temp is None or entry.current > best_temp:
                                best_temp = entry.current
            if best_temp is None:
                for name, entries in temps.items():
                    for entry in entries:
                        if entry.current is not None and 'Core' in entry.label:
                            if best_temp is None or entry.current > best_temp:
                                best_temp = entry.current
            if best_temp is None:
                for name, entries in temps.items():
                    if entries and entries[0].current is not None:
                        best_temp = entries[0].current
                        break
            temp_c = best_temp
    except Exception:
        pass

    model = 'Unknown'
    try:
        import cpuinfo
        info = cpuinfo.get_cpu_info()
        model = info.get('brand_raw', 'Unknown')
    except Exception:
        pass

    return {
        'model': model,
        'physical_cores': cpu_count_phys,
        'logical_cores': cpu_count_log,
        'load_avg': list(load_avg),
        'utilization_pct': cpu_percent,                    # ← Derived aggregate (backward compatible)
        'utilization_per_core_pct': cpu_per_core,           # ← NEW: list of floats per logical core
        'temp_c': temp_c,                                   # ← Existing aggregate temp (backward compatible)
        'temp_per_core_c': cpu_temp_per_core,               # ← NEW: list of temps per logical core
        'freq': cpu_freq,                                   # ← Existing aggregate freq (backward compatible)
        'freq_per_core': cpu_freq_per_core,                 # ← NEW: list of {current,min,max} per logical core
    }
```

**Key Decisions:**
- **Single blocking call** `psutil.cpu_percent(interval=1, percpu=True)` — measures actual per-core usage over 1 second
- **Aggregate derived** as arithmetic mean of per-core values — semantically equivalent to old aggregate call
- **No double-blocking**: Only 1 second total (not 2) per collection cycle
- **Per-core frequency**: `psutil.cpu_freq(percpu=True)` returns list of namedtuples; convert to list of dicts
- **Per-core temperature**: Parse `sensors_temperatures()` for 'coretemp'/'k10temp' sensors with "Core N" labels; map to logical core indices
- **Backward compatibility**: All existing fields (`utilization_pct`, `temp_c`, `freq`) preserved and populated
- `cpu_per_core` length == `logical_cores` (consistent ordering guaranteed by psutil)
- First call with `interval=1` returns real measured values (not zeros) — no warm-up issue
- On platforms without per-core freq/temp support, new fields are empty lists `[]`

**Version Bump:**
- `__version__ = '1.15.0'` (MINOR: new payload fields)
- `__schema_version__ = '1.20'` (MINOR: new fields in cpu object)

---

### Step 2: Server Models — Add Per-Core Fields

**File:** `gpu_monitor/metrics_app/models.py`

#### A. MetricSnapshot (time-series)

```python
class MetricSnapshot(models.Model):
    # ... existing fields ...
    
    # NEW: Per-core utilization (JSON array, one float per logical core)
    # Index 0 = logical core 0, index 1 = logical core 1, etc.
    cpu_utilization_per_core_json = models.JSONField(default=list, blank=True)
    
    # NEW: Per-core temperature (JSON array, one float per logical core, °C)
    cpu_temp_per_core_json = models.JSONField(default=list, blank=True)
    
    # NEW: Per-core frequency (JSON array, one object per logical core)
    # Each object: {current_mhz, min_mhz, max_mhz}
    cpu_freq_per_core_json = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'metrics_metricsnapshot'
        # ... existing indexes ...
```

#### B. LatestSnapshot (denormalized)

```python
class LatestSnapshot(models.Model):
    # ... existing fields ...
    
    # NEW: Per-core utilization for Live Metrics display
    cpu_utilization_per_core_json = models.JSONField(default=list, blank=True)
    
    # NEW: Per-core temperature for Live Metrics display
    cpu_temp_per_core_json = models.JSONField(default=list, blank=True)
    
    # NEW: Per-core frequency for Live Metrics display
    cpu_freq_per_core_json = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'metrics_latest_snapshot'
```

**Migration Required:** Generate migration `0053_add_cpu_per_core_fields.py`

---

### Step 3: Serializer — Ingest Per-Core Data

**File:** `gpu_monitor/metrics_app/serializers.py`

#### A. In `process_ingest()` - Extract per-core from payload

```python
# Around line 63 (after cpu = metrics_data.get('cpu', {}))
cpu = metrics_data.get('cpu', {})
cpu_per_core = cpu.get('utilization_per_core_pct', [])           # NEW
cpu_temp_per_core = cpu.get('temp_per_core_c', [])               # NEW
cpu_freq_per_core = cpu.get('freq_per_core', [])                 # NEW
```

#### B. In MetricSnapshot defaults (around line 105-125)

```python
defaults = {
    # ... existing ...
    'cpu_utilization_pct': cpu.get('utilization_pct'),
    'cpu_utilization_per_core_json': cpu_per_core,        # NEW
    'cpu_temp_per_core_json': cpu_temp_per_core,          # NEW
    'cpu_freq_per_core_json': cpu_freq_per_core,          # NEW
    # ... rest unchanged ...
}
```

#### C. In LatestSnapshot defaults (around line 522-535)

```python
ls_defaults = {
    # ... existing ...
    'cpu_utilization_pct': cpu.get('utilization_pct'),
    'cpu_utilization_per_core_json': cpu_per_core,        # NEW
    'cpu_temp_per_core_json': cpu_temp_per_core,          # NEW
    'cpu_freq_per_core_json': cpu_freq_per_core,          # NEW
    # ... rest unchanged ...
}
```

---

### Step 4: Chart Data View — New Per-Core Metrics

**File:** `gpu_monitor/metrics_app/views.py`

#### A. Add to `SNAPSHOT_METRICS` (line 226-232)

```python
SNAPSHOT_METRICS = frozenset({
    'cpu_utilization_pct', 'cpu_temp_c', 'cpu_freq_current_mhz',
    'mem_total_bytes', 'mem_used_bytes', 'mem_free_bytes', 'mem_cached_bytes',
    'swap_used_bytes', 'swap_total_bytes',
    'cpu_power_w', 'total_system_power_w',
    'has_active_job',
    # NEW: Per-core metrics
    'cpu_utilization_per_core_pct',   # Per-core utilization chart
    'cpu_temp_per_core_c',            # Per-core temperature chart
    'cpu_freq_per_core_current_mhz',  # Per-core current frequency chart
})
```

#### B. Add handler in `_handle_snapshot_metric()` (around line 622-638)

```python
# NEW: Handle per-core utilization metric (multi-series chart)
if metric == 'cpu_utilization_per_core_pct':
    # Returns multiple datasets — one per logical core
    # Uses single GROUP BY query with JSON extraction
    trunc = self._trunc_for_bucket(bucket_minutes)
    bucket_seconds = bucket_minutes * 60
    
    rows = MetricSnapshot.objects.filter(
        rig_uuid=uuid,
        timestamp__gte=start_bucket,
        timestamp__lte=end_bucket,
    ).annotate(bucket=trunc('timestamp')).values(
        'bucket', 'cpu_utilization_per_core_json'
    ).order_by('bucket')
    
    # Determine max cores from first non-empty row
    max_cores = 0
    for row in rows:
        if row['cpu_utilization_per_core_json']:
            max_cores = max(max_cores, len(row['cpu_utilization_per_core_json']))
            break
    
    if max_cores == 0:
        return {'labels': labels, 'datasets': []}
    
    # Build datasets: one per core
    datasets = [
        {'label': f'Core {i}', 'data': [None] * total_buckets}
        for i in range(max_cores)
    ]
    
    for row in rows:
        idx = self._bucket_index(row['bucket'], start_bucket, bucket_seconds)
        if idx is None or idx >= total_buckets:
            continue
        per_core = row['cpu_utilization_per_core_json']
        if not per_core:
            continue
        for core_idx, val in enumerate(per_core):
            if core_idx < max_cores and val is not None:
                datasets[core_idx]['data'][idx] = round(val, 2)
    
    return {'labels': labels, 'datasets': datasets}

# NEW: Handle per-core temperature metric (multi-series chart)
if metric == 'cpu_temp_per_core_c':
    trunc = self._trunc_for_bucket(bucket_minutes)
    bucket_seconds = bucket_minutes * 60
    
    rows = MetricSnapshot.objects.filter(
        rig_uuid=uuid,
        timestamp__gte=start_bucket,
        timestamp__lte=end_bucket,
    ).annotate(bucket=trunc('timestamp')).values(
        'bucket', 'cpu_temp_per_core_json'
    ).order_by('bucket')
    
    max_cores = 0
    for row in rows:
        if row['cpu_temp_per_core_json']:
            max_cores = max(max_cores, len(row['cpu_temp_per_core_json']))
            break
    
    if max_cores == 0:
        return {'labels': labels, 'datasets': []}
    
    datasets = [
        {'label': f'Core {i}', 'data': [None] * total_buckets}
        for i in range(max_cores)
    ]
    
    for row in rows:
        idx = self._bucket_index(row['bucket'], start_bucket, bucket_seconds)
        if idx is None or idx >= total_buckets:
            continue
        per_core = row['cpu_temp_per_core_json']
        if not per_core:
            continue
        for core_idx, val in enumerate(per_core):
            if core_idx < max_cores and val is not None:
                datasets[core_idx]['data'][idx] = round(val, 1)
    
    return {'labels': labels, 'datasets': datasets}

# NEW: Handle per-core frequency metric (multi-series chart)
if metric == 'cpu_freq_per_core_current_mhz':
    trunc = self._trunc_for_bucket(bucket_minutes)
    bucket_seconds = bucket_minutes * 60
    
    rows = MetricSnapshot.objects.filter(
        rig_uuid=uuid,
        timestamp__gte=start_bucket,
        timestamp__lte=end_bucket,
    ).annotate(bucket=trunc('timestamp')).values(
        'bucket', 'cpu_freq_per_core_json'
    ).order_by('bucket')
    
    max_cores = 0
    for row in rows:
        if row['cpu_freq_per_core_json']:
            max_cores = max(max_cores, len(row['cpu_freq_per_core_json']))
            break
    
    if max_cores == 0:
        return {'labels': labels, 'datasets': []}
    
    datasets = [
        {'label': f'Core {i}', 'data': [None] * total_buckets}
        for i in range(max_cores)
    ]
    
    for row in rows:
        idx = self._bucket_index(row['bucket'], start_bucket, bucket_seconds)
        if idx is None or idx >= total_buckets:
            continue
        per_core = row['cpu_freq_per_core_json']
        if not per_core:
            continue
        for core_idx, freq_obj in enumerate(per_core):
            if core_idx < max_cores and freq_obj and freq_obj.get('current_mhz') is not None:
                datasets[core_idx]['data'][idx] = round(freq_obj['current_mhz'], 0)
    
    return {'labels': labels, 'datasets': datasets}
```

#### C. Add helper for core count in chart metadata (optional)

```python
# Could add a new metric 'cpu_core_count' that returns the number of cores
# Useful for frontend to know how many series to expect
```

---

### Step 5: Live Metrics — Display Per-Core Bars

**File:** `gpu_monitor/dashboard/views.py`

#### A. In `_fetch_rig_metrics()` - No change needed (snapshot already has the JSON arrays)

The `LatestSnapshot.cpu_utilization_per_core_json`, `cpu_temp_per_core_json`, and `cpu_freq_per_core_json` will be available via `snapshot` object.

#### B. Template: `gpu_monitor/templates/dashboard/_metrics_cards.html`

**Current CPU section (lines 89-157):** Add per-core visualization after aggregate bar.

```html
<!-- ════════════════════════════════════════════════════════════════ -->
<!-- CPU -->
<!-- ════════════════════════════════════════════════════════════════ -->
<div class="bg-gray-800 border border-gray-700 rounded-lg p-4 mb-4">
    <h3 class="text-gray-300 text-sm font-medium mb-2">
        CPU
        {% if is_data_stale %}
        <span class="text-xs text-yellow-400 ml-1">(stale)</span>
        {% endif %}
    </h3>
    {% if snapshot.cpu_model %}
    <div class="text-lg font-bold text-white mb-1">
        {{ snapshot.cpu_model|default:"Unknown" }}
    </div>
    <div class="text-sm text-gray-300 mb-2">
        {{ snapshot.cpu_physical_cores|default:"?" }} cores / {{ snapshot.cpu_logical_cores|default:"?" }} threads
    </div>
    
    <!-- AGGREGATE BAR (existing) -->
    <div class="flex items-center gap-3 mb-2">
        <div class="flex-1">
            <div class="w-full bg-gray-700 rounded-full h-2 overflow-hidden">
                <div class="h-full rounded-full transition-all duration-200 ease-out {{ snapshot.cpu_utilization_pct|tier_fill:cpu_util_t|default:"bg-gray-400" }}"
                    style="width: {% if snapshot.cpu_utilization_pct != None %}{{ snapshot.cpu_utilization_pct }}{% else %}0{% endif %}%">
                </div>
            </div>
        </div>
        <span class="text-sm font-mono w-14 text-right">
            {% if snapshot.cpu_utilization_pct != None %}
            <span class="{{ snapshot.cpu_utilization_pct|tier_text:cpu_util_t }}">{{ snapshot.cpu_utilization_pct|floatformat:1 }}%</span>
            {% else %}—{% endif %}
        </span>
    </div>
    
    <!-- NEW: PER-CORE BARS (collapsible) -->
    {% if snapshot.cpu_utilization_per_core_json %}
    <details class="mb-2 group">
        <summary class="cursor-pointer text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
            <span>Per-Core Utilization ({{ snapshot.cpu_logical_cores }} logical cores)</span>
            <svg class="w-4 h-4 transition-transform group-open:rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
        </summary>
        <div class="mt-2 space-y-1 max-h-64 overflow-y-auto">
            {% for core_val in snapshot.cpu_utilization_per_core_json %}
            <div class="flex items-center gap-2 text-xs">
                <span class="font-mono text-gray-400 w-8 text-right">Core {{ forloop.counter0 }}:</span>
                <div class="flex-1 bg-gray-700 rounded-full h-1.5 overflow-hidden">
                    <div class="h-full rounded-full transition-all duration-200 ease-out {{ core_val|tier_fill:cpu_util_t|default:"bg-gray-400" }}"
                         style="width: {% if core_val != None %}{{ core_val }}{% else %}0{% endif %}%"></div>
                </div>
                <span class="font-mono text-right w-10 {% if core_val != None %}{{ core_val|tier_text:cpu_util_t }}{% endif %}">
                    {% if core_val != None %}{{ core_val|floatformat:1 }}%{% else %}—{% endif %}
                </span>
            </div>
            {% endfor %}
        </div>
    </details>
    {% endif %}
    
    <!-- NEW: PER-CORE TEMPERATURE (collapsible) -->
    {% if snapshot.cpu_temp_per_core_json %}
    <details class="mb-2 group">
        <summary class="cursor-pointer text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
            <span>Per-Core Temperature ({{ snapshot.cpu_logical_cores }} logical cores)</span>
            <svg class="w-4 h-4 transition-transform group-open:rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
        </summary>
        <div class="mt-2 space-y-1 max-h-64 overflow-y-auto">
            {% for core_temp in snapshot.cpu_temp_per_core_json %}
            <div class="flex items-center gap-2 text-xs">
                <span class="font-mono text-gray-400 w-8 text-right">Core {{ forloop.counter0 }}:</span>
                <div class="flex-1 bg-gray-700 rounded-full h-1.5 overflow-hidden">
                    <div class="h-full rounded-full transition-all duration-200 ease-out {{ core_temp|tier_fill:cpu_temp_t|default:"bg-gray-400" }}"
                         style="width: {% if core_temp != None %}{{ core_temp|divide:100|floatformat:0 }}{% else %}0{% endif %}%"></div>
                </div>
                <span class="font-mono text-right w-10 {% if core_temp != None %}{{ core_temp|tier_text:cpu_temp_t }}{% endif %}">
                    {% if core_temp != None %}{{ core_temp|floatformat:1 }}°C{% else %}—{% endif %}
                </span>
            </div>
            {% endfor %}
        </div>
    </details>
    {% endif %}
    
    <!-- NEW: PER-CORE FREQUENCY (collapsible) -->
    {% if snapshot.cpu_freq_per_core_json %}
    <details class="mb-2 group">
        <summary class="cursor-pointer text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
            <span>Per-Core Frequency ({{ snapshot.cpu_logical_cores }} logical cores)</span>
            <svg class="w-4 h-4 transition-transform group-open:rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
        </summary>
        <div class="mt-2 space-y-1 max-h-64 overflow-y-auto">
            {% for freq_obj in snapshot.cpu_freq_per_core_json %}
            <div class="flex items-center gap-2 text-xs">
                <span class="font-mono text-gray-400 w-8 text-right">Core {{ forloop.counter0 }}:</span>
                {% if freq_obj.current_mhz != None %}
                <span class="text-cyan-400 font-mono">{{ freq_obj.current_mhz|floatformat:0 }} MHz</span>
                {% if freq_obj.min_mhz or freq_obj.max_mhz %}
                <span class="text-gray-600">({{ freq_obj.min_mhz|floatformat:0|default:"?" }}-{{ freq_obj.max_mhz|floatformat:0|default:"?" }})</span>
                {% endif %}
                {% else %}
                <span class="text-gray-500">—</span>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </details>
    {% endif %}
    
    <!-- Existing: Temp, Freq, Load -->
    <div class="flex items-center gap-4 text-xs text-gray-500">
        <span>
            Temp:
            {% if snapshot.cpu_temp_c != None %}
                <span class="{{ snapshot.cpu_temp_c|tier_text:cpu_temp_t }}">
                    {{ snapshot.cpu_temp_c|floatformat:1 }}°C
                </span>
            {% else %}—{% endif %}
        </span>
        {% if snapshot.cpu_freq_current_mhz != None %}
        <span>
            Freq:
            <span class="text-cyan-400">
                {{ snapshot.cpu_freq_current_mhz|floatformat:0 }} MHz
            </span>
            {% if snapshot.cpu_freq_min_mhz or snapshot.cpu_freq_max_mhz %}
            <span class="text-gray-600">
                ({{ snapshot.cpu_freq_min_mhz|floatformat:0|default:"?" }}-{{ snapshot.cpu_freq_max_mhz|floatformat:0|default:"?" }})
            </span>
            {% endif %}
        </span>
        {% endif %}
        {% if snapshot.cpu_load_avg_json %}
        <span>
            Load:
            {% for val in snapshot.cpu_load_avg_json %}
                <span class="text-gray-300">{{ val|floatformat:2 }}</span>{% if not forloop.last %} / {% endif %}
            {% endfor %}
        </span>
        {% endif %}
    </div>
    {% elif snapshot %}
    <div class="text-sm text-gray-300 mb-2">
        Utilization: {% if snapshot.cpu_utilization_pct != None %}
        <span class="{{ snapshot.cpu_utilization_pct|tier_text:cpu_util_t }}">{{ snapshot.cpu_utilization_pct|floatformat:1 }}%</span>
        {% else %}—{% endif %}
    </div>
    {% else %}
    <div class="text-gray-500">No data</div>
    {% endif %}
</div>
```

---

### Step 6: Django System Checks — Validate New Fields

**File:** `gpu_monitor/metrics_app/checks.py`

Add checks following the existing W001/W004/0052 defense pattern:

```python
# In existing check functions, add:
# Layer 1: MetricSnapshot has all three per-core JSON fields
# Layer 2: LatestSnapshot has all three per-core JSON fields  
# Layer 3: ChartDataView.SNAPSHOT_METRICS includes all three per-core metrics
# Layer 4: compact_data knows about these fields (or explicitly excludes them since they're JSON)

# Example check structure:
from django.core.checks import Warning, register
from django.apps import apps

@register()
def check_cpu_per_core_fields(app_configs, **kwargs):
    errors = []
    try:
        MetricSnapshot = apps.get_model('metrics_app', 'MetricSnapshot')
        LatestSnapshot = apps.get_model('metrics_app', 'LatestSnapshot')
        
        # Check MetricSnapshot fields
        required_ms_fields = [
            'cpu_utilization_per_core_json',
            'cpu_temp_per_core_json',
            'cpu_freq_per_core_json',
        ]
        for field_name in required_ms_fields:
            if not hasattr(MetricSnapshot, field_name):
                errors.append(Warning(
                    f'MetricSnapshot missing {field_name} field',
                    hint='Run migrations to add per-core CPU fields',
                    id=f'metrics_app.W05{required_ms_fields.index(field_name) + 3}',
                ))
        
        # Check LatestSnapshot fields
        required_ls_fields = [
            'cpu_utilization_per_core_json',
            'cpu_temp_per_core_json',
            'cpu_freq_per_core_json',
        ]
        for field_name in required_ls_fields:
            if not hasattr(LatestSnapshot, field_name):
                errors.append(Warning(
                    f'LatestSnapshot missing {field_name} field',
                    hint='Run migrations to add per-core CPU fields',
                    id=f'metrics_app.W05{required_ls_fields.index(field_name) + 6}',
                ))
        
        # Check ChartDataView
        from metrics_app.views import ChartDataView
        required_chart_metrics = [
            'cpu_utilization_per_core_pct',
            'cpu_temp_per_core_c',
            'cpu_freq_per_core_current_mhz',
        ]
        for metric_name in required_chart_metrics:
            if metric_name not in ChartDataView.SNAPSHOT_METRICS:
                errors.append(Warning(
                    f'ChartDataView.SNAPSHOT_METRICS missing {metric_name}',
                    hint=f'Add {metric_name} to SNAPSHOT_METRICS',
                    id=f'metrics_app.W05{required_chart_metrics.index(metric_name) + 9}',
                ))
    except Exception:
        pass
    return errors
```

---

### Step 7: Compact Data — Handle Per-Core JSON

**File:** `gpu_monitor/metrics_app/management/commands/compact_data.py`

The per-core fields (`cpu_utilization_per_core_json`, `cpu_temp_per_core_json`, `cpu_freq_per_core_json`) are **JSON arrays** — they cannot be meaningfully averaged across buckets like scalar values. 

**Decision: Use 'last' aggregation** — Preserve the most recent per-core array in each bucket (same approach as `cpu_load_avg_json`). This allows per-core charts to work at all time ranges (tier 2/3) while keeping storage efficient.

```python
# In COMPACT_TABLES for metrics_metricsnapshot:
'cpu_utilization_per_core_json': 'last',
'cpu_temp_per_core_json': 'last',
'cpu_freq_per_core_json': 'last',
```

This follows the same pattern as other JSON array fields in the time-series tables.

---

### Step 8: Windows Agent Parity

**File:** `agent_windows/run.py`

Apply identical changes to `collect_cpu()` function for Windows agent compatibility. Windows psutil supports `percpu=True` identically for `cpu_percent`. Note: `cpu_freq(percpu=True)` and `sensors_temperatures()` may have limited support on Windows — wrap in try/except and return empty lists if unavailable.

---

## Data Flow Summary

```
┌─────────────────────────────────────────────────────────────────┐
│ AGENT (run.py)                                                  │
│   cpu_per_core = psutil.cpu_percent(interval=1, percpu=True)   │
│   cpu_freq_per_core = psutil.cpu_freq(percpu=True)             │
│   cpu_temp_per_core = parse sensors_temperatures()              │
│   payload: {cpu: {utilization_per_core_pct: [...],             │
│                   temp_per_core_c: [...],                       │
│                   freq_per_core: [{current,min,max}, ...]}}     │
└─────────────────────┬───────────────────────────────────────────┘
                      │ HTTP POST /api/v1/ingest/
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│ SERIALIZER (serializers.py:process_ingest)                     │
│   cpu_per_core = cpu.get('utilization_per_core_pct', [])       │
│   cpu_temp_per_core = cpu.get('temp_per_core_c', [])           │
│   cpu_freq_per_core = cpu.get('freq_per_core', [])             │
│   MetricSnapshot.defaults[..._per_core_json] = ...             │
│   LatestSnapshot.defaults[..._per_core_json] = ...             │
└─────────────────────┬───────────────────────────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
┌───────────────────┐     ┌─────────────────────┐
│ MetricSnapshot    │     │ LatestSnapshot      │
│ (time-series)     │     │ (denormalized)      │
│ cpu_utilization_  │     │ cpu_utilization_    │
│ _per_core_json    │     │ _per_core_json      │
│ cpu_temp_per_core │     │ cpu_temp_per_core   │
│ _json             │     │ _json               │
│ cpu_freq_per_core │     │ cpu_freq_per_core   │
│ _json             │     │ _json               │
└─────────┬─────────┘     └──────────┬──────────┘
          │                          │
          ▼                          ▼
┌───────────────────┐     ┌─────────────────────┐
│ ChartDataView     │     │ Live Metrics        │
│ _handle_snapshot_ │     │ _metrics_cards.html │
│ metric()          │     │ <details> per-core  │
│ multi-series for  │     │ bars (util/temp/    │
│ util/temp/freq    │     │ freq)               │
└───────────────────┘     └─────────────────────┘
```

---

## Payload Examples

### Agent → Server (schema_version 1.20)

```json
{
  "rig_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "schema_version": "1.20",
  "agent_version": "1.15.0",
  "timestamp": "2026-10-06T12:00:00Z",
  "metrics": {
    "cpu": {
      "model": "AMD Ryzen 9 5950X",
      "physical_cores": 16,
      "logical_cores": 32,
      "load_avg": [1.23, 1.45, 1.67],
      "utilization_pct": 42.5,
      "utilization_per_core_pct": [12.5, 45.2, 8.1, 67.3, 23.4, 56.7, 11.2, 34.5,
                                    9.8, 78.9, 45.6, 12.3, 56.7, 23.4, 67.8, 34.5,
                                    12.5, 45.2, 8.1, 67.3, 23.4, 56.7, 11.2, 34.5,
                                    9.8, 78.9, 45.6, 12.3, 56.7, 23.4, 67.8, 34.5],
      "temp_c": 58.2,
      "temp_per_core_c": [45.0, 52.0, 44.0, 55.0, 43.0, 50.0, 46.0, 48.0,
                           44.0, 56.0, 47.0, 42.0, 49.0, 45.0, 53.0, 46.0,
                           45.0, 52.0, 44.0, 55.0, 43.0, 50.0, 46.0, 48.0,
                           44.0, 56.0, 47.0, 42.0, 49.0, 45.0, 53.0, 46.0],
      "freq": {"current_mhz": 3400, "min_mhz": 2200, "max_mhz": 4900},
      "freq_per_core": [
        {"current_mhz": 3400, "min_mhz": 2200, "max_mhz": 4900},
        {"current_mhz": 3300, "min_mhz": 2200, "max_mhz": 4900},
        {"current_mhz": 3500, "min_mhz": 2200, "max_mhz": 4900},
        {"current_mhz": 3200, "min_mhz": 2200, "max_mhz": 4900},
        ...
      ]
    }
  }
}
```

### Chart API Responses (range=24h)

**Per-Core Utilization:**
```json
{
  "labels": ["12:00", "12:01", ..., "11:59"],
  "datasets": [
    {"label": "Core 0", "data": [12.5, 13.1, ..., null, 11.8]},
    {"label": "Core 1", "data": [45.2, 44.8, ..., null, 46.2]},
    ...
    {"label": "Core 31", "data": [34.5, 35.1, ..., null, 33.9]}
  ]
}
```

**Per-Core Temperature:**
```json
{
  "labels": ["12:00", "12:01", ..., "11:59"],
  "datasets": [
    {"label": "Core 0", "data": [45.0, 45.5, ..., null, 44.8]},
    {"label": "Core 1", "data": [52.0, 51.8, ..., null, 52.3]},
    ...
  ]
}
```

**Per-Core Frequency:**
```json
{
  "labels": ["12:00", "12:01", ..., "11:59"],
  "datasets": [
    {"label": "Core 0", "data": [3400, 3380, ..., null, 3420]},
    {"label": "Core 1", "data": [3300, 3320, ..., null, 3280]},
    ...
  ]
}
```

### Live Metrics (LatestSnapshot)

```python
snapshot.cpu_utilization_per_core_json  # [12.5, 45.2, 8.1, ...]
snapshot.cpu_temp_per_core_json         # [45.0, 52.0, 44.0, ...]
snapshot.cpu_freq_per_core_json         # [{"current_mhz": 3400, ...}, ...]
snapshot.cpu_logical_cores              # 32
```

---

## Testing Strategy

### Unit Tests

1. **Agent:** Test `collect_cpu()` returns all three per-core arrays (`utilization_per_core_pct`, `temp_per_core_c`, `freq_per_core`) of correct length matching `logical_cores`
2. **Serializer:** Test ingestion with/without per-core fields (backward compat)
3. **Models:** Test migration applies, fields accept JSON arrays
4. **Chart View:** Test `_handle_snapshot_metric` with all three per-core metrics returns multi-series
5. **Template:** Test `_metrics_cards.html` renders all three collapsible per-core sections when data present

### Integration Tests

1. Full ingest → Chart API → Verify per-core data flows through for all three metrics
2. Test with 1, 4, 16, 32, 64 logical cores
3. Test missing per-core fields (older agents) → Graceful degradation (empty arrays)
4. Test empty per-core arrays → No charts, no errors
5. Test Windows agent (no per-core freq/temp) → Empty arrays, no errors

### Manual Verification

1. Deploy agent with new version to test rig
2. Verify payload contains `utilization_per_core_pct`, `temp_per_core_c`, `freq_per_core`
3. Check MetricSnapshot row has all three JSON arrays
4. Check LatestSnapshot has all three JSON arrays
5. Open Live Metrics → Verify three collapsible per-core sections (Utilization, Temperature, Frequency)
6. Open Charts → Select all three per-core metrics → Verify multi-line charts

---

## Rollout Plan

### Phase 1: Agent + Server Deploy (Atomic)
1. Create branch `plan/per-cpu-core-statistics`
2. Implement all code changes
3. Generate migration `0053_add_cpu_per_core_fields.py`
4. Test locally with dev DB
5. Push branch, create PR

### Phase 2: Migration + Deploy
1. Apply migration to production
2. Deploy server code
3. Deploy agent update (can be gradual — old agents still work)

### Phase 3: Verification
1. Verify new agents sending per-core data
2. Verify charts work
3. Verify Live Metrics display works

---

## Backward Compatibility

| Scenario | Behavior |
|----------|----------|
| Old agent (no per-core) + New server | `cpu_utilization_per_core_json` = `[]` (empty list). Charts show no per-core series. Live Metrics collapsible section hidden. |
| New agent + Old server (no migration) | Server ignores unknown field (DRF JSONField allows extra). No error. |
| New agent + New server (migration applied) | Full functionality. |

---

## Defense (W001/W004/0052) Checklist

- [ ] **Code:** Agent collects per-core, server stores in both models
- [ ] **Django Check:** `checks.py` validates field existence + ChartDataView inclusion
- [ ] **Documentation:** This plan document + update architecture docs if needed
- [ ] **Skill:** Update `gpu-rig-monitoring` skill with per-core pattern

---

## File Change Summary

| File | Changes |
|------|---------|
| `agent/run.py` | Add `utilization_per_core_pct`, `temp_per_core_c`, `freq_per_core` to `collect_cpu()` return dict; bump version |
| `agent_windows/run.py` | Same as above (with try/except for per-core freq/temp) |
| `gpu_monitor/metrics_app/models.py` | Add `cpu_utilization_per_core_json`, `cpu_temp_per_core_json`, `cpu_freq_per_core_json` to `MetricSnapshot` + `LatestSnapshot` |
| `gpu_monitor/metrics_app/serializers.py` | Ingest all three per-core arrays into both snapshots |
| `gpu_monitor/metrics_app/views.py` | Add three per-core metrics to `SNAPSHOT_METRICS`; add three handlers in `_handle_snapshot_metric` |
| `gpu_monitor/metrics_app/checks.py` | Add system checks for all three per-core fields |
| `gpu_monitor/templates/dashboard/_metrics_cards.html` | Add three collapsible per-core sections (Utilization, Temperature, Frequency) in CPU section |
| `gpu_monitor/metrics_app/migrations/0053_*.py` | Auto-generated migration |

---

## Open Questions / Decisions Needed

1. **Bucket aggregation for per-core in charts:** Current plan uses raw values per bucket (no AVG across cores). Is this desired, or should we also provide an "average across cores" line?

2. **Core ordering:** psutil guarantees consistent ordering by logical core index. Is this sufficient, or do we need to map to physical core topology (core 0 = thread 0 of physical core 0, core 1 = thread 1 of physical core 0, etc.)?

3. **Color coding:** Use same `cpu_util` thresholds for per-core utilization bars, `cpu_temp` for temperature bars? (Current plan: yes)

4. **Chart defaults:** Should per-core charts be new metric options, or replace the aggregate charts? (Plan: new metric options `cpu_utilization_per_core_pct`, `cpu_temp_per_core_c`, `cpu_freq_per_core_current_mhz`)

5. **Windows agent:** `cpu_freq(percpu=True)` and `sensors_temperatures()` may have limited support on Windows — confirm behavior and ensure graceful degradation (empty arrays).

6. **Temperature sensor mapping:** The plan maps "Core N" labels from coretemp/k10temp to logical core indices. On some systems (AMD vs Intel, hyperthreading), the mapping may not be 1:1. Acceptable?

---

## Approval Required

- [ ] User approves plan
- [ ] Branch created: `plan/per-cpu-core-statistics`
- [ ] Implementation begins after approval

---