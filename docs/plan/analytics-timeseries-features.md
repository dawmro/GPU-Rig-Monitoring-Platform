# Analytics Timeseries Features — Analysis & Architecture Plan

## 1. Executive Summary

This document analyzes proposed analytics metrics derived from GPU rig timeseries data, categorizes them by **where they belong** (Historical Charts subtab vs. report tab), and defines the **calculation architecture** — whether values are pre-computed at ingest time (stored in new DB models) or computed on-demand when the user opens the report tab.

## 2. Architecture Decision: Two Calculation Modes

### Mode A: Pre-computed at Ingest (for Chart subtab metrics)

**What it is:** The serializer computes derived metrics during payload ingestion and stores them in new database models alongside the raw timeseries data. Charts fetch pre-computed values directly — no aggregation needed at chart-render time.

**Pros:**
- Charts always show complete data (no gaps from on-demand calculation failures)
- Consistent with existing chart architecture (raw + compacted timeseries, fetched via ChartDataView)
- Zero additional latency for chart rendering
- Values are available immediately after ingest — no waiting for aggregation windows

**Cons:**
- Additional DB writes per ingest (more load on the ingest path)
- New models need their own compaction strategy
- Some metrics (e.g., Pearson correlation) need windowed data that isn't available at single-point ingest time

**Verdict: Suitable for single-point derived metrics** (ratios, differences, flags computable from one payload row). **Not suitable for windowed/cumulative metrics** (correlations, slopes, transition counts).

### Mode B: On-Demand Calculation (for Report tab)

**What it is:** When the user opens the Report tab, the server runs SQL aggregation queries against the existing timeseries tables and computes derived metrics in Python. Results are cached at the view level (55s TTL, matching existing report caching pattern).

**Pros:**
- No additional ingest load
- Works with already-compacted data (15-min / 1-hour buckets for 7d/30d ranges)
- Leverages existing `_build_report_context` pattern — minimal new code
- Can compute complex metrics (correlations, linear regression slopes, transition counts) that need multi-row windows

**Cons:**
- Additional query load when user opens the tab (acceptable — tab is opened on demand, not polling)
- Slightly slower than pre-computed (but still fast with compacted data)
- 30d range on MetricSnapshot (not compacted) = ~43K rows — needs careful query design

**Verdict: Suitable for all report-tab metrics.** The existing report already does on-the-fly aggregation for 5 queries; adding 2-3 more for derived metrics is proportionate.


## 3. Metric Classification: Charts vs. Report Tab

### 3.1 Metrics That Belong in Historical Charts Subtab

These are **time-series visualizations** — they plot a derived value over time, just like existing charts (GPU Temp, Fan Speed, etc.). They follow the same pattern: fetch from ChartDataView, render with Chart.js.

| # | Metric                                  | Chart Name                    | Data Source | Calculation                                                                                              | Chart Type    |
| - | --------------------------------------- | ----------------------------- | ----------- | -------------------------------------------------------------------------------------------------------- | ------------- |
| 1 | **Cooling Efficiency Index**            | GPU Cooling Efficiency        | GPUMetric   | `ΔTemp / ΔPower` = temperature change per watt of GPU power change                                       | Line, per-GPU |
| 2 | **Fan-Adjusted Cooling Response Index** | Fan-Adjusted Cooling Response | GPUMetric   | `(ΔTemp / ΔPower) / (1 + ΔFan% / 100)` = temperature response per watt, adjusted for change in fan speed | Line, per-GPU |
| 3 | **VRAM Bandwidth Saturation Index** | VRAM Bandwidth Saturation | GPUMetric | `mem_controller_util_pct / gpu_util_pct` | Line, per-GPU |
| 4 | **CPU-to-GPU Power Ratio** | CPU/GPU Power Ratio | GPUMetric + MetricSnapshot | `power_cpu_w / power_gpu_w` (or `cpu_power_w / sum(power_draw_w)`) | Line, single |

**Rationale for Chart placement:** These are **point-in-time derived metrics** — each data point is computed from a single snapshot row. Delatas can be pre-computed at ingest time from last snapshot json vs current payload and stored as new fields on GPUMetric (or a new AnalyticsMetric model), then exposed through the existing ChartDataView → chart-registry pipeline with zero changes to the fetch/render flow.

### 3.2 Metrics That Belong in Report Tab

These are **aggregate statistics over a time window** (24h/7d/30d). They don't make sense as continuous time-series charts — they're summary numbers for a period. They belong in the Report tab in per gpu section or System section.

| # | Metric | Calculation Method | SQL/Python |
|---|--------|-------------------|-------------|
| 1 | **Cooling Efficiency Index** | Avg of `GPUMetric.cooling_efficiency_index` for a given period 24h, 7d, 30d per gpu | Avg |
| 2 | **Fan-Adjusted Cooling Response Index** | Avg of `GPUMetric.fan_adjusted_cooling_response` for a given period 24h, 7d, 30d per gpu | Avg |
| 3 | **Temperature-to-PowerLimit Ratio Stability** | Std dev of `power_limit_w` grouped by `gpu_temp_c` ranges | SQL: GROUP BY temp bucket, compute STDDEV(power_limit_w) |
| 4 | **Memory vs. Core Utilization Correlation** | Pearson r between `mem_controller_util_pct` and `gpu_util_pct` | Python: `scipy.stats.pearsonr` or manual formula |
| 5 | **Compute-to-Memory Ratio Trend** | Rolling average of `gpu_util_pct / mem_controller_util_pct` over time | Python: compute ratio per bucket, then linear regression slope |
| 6 | **Clock Stability Index** | Std dev of `gpu_core_clock_mhz` over rolling windows | SQL: STDDEV(gpu_core_clock_mhz) grouped by time window |
| 7 | **Frequency-to-PowerLimit Ratio Stability** | Std dev of `power_limit_w` grouped by `gpu_core_clock_mhz` ranges | SQL: GROUP BY clock bucket, STDDEV(power_limit_w) |
| 8 | **Idle-to-Peak Power Delta** | `Max(power_draw_w) - Min(power_draw_w)` over the period | SQL: `Max - Min` in the existing GPU aggregation query |
| 9 | **Idle Power Waste Ratio** | `Avg(power_draw_w when has_active_job=False) / Avg(power_draw_w when has_active_job=True)` | SQL: conditional aggregation |
| 10 | **Job State Transition Frequency** | Count of `has_active_job` state changes (0→1 and 1→0) | Python: ordered scan of `has_active_job` values |
| 11 | **Underutilization Duration** | Sum of continuous minutes where `gpu_util_pct < 5` AND `has_active_job=True` | Python: scan ordered rows, accumulate gaps |
| 12 | **Power-on Hours Before Restart** | Max `uptime_s` before a drop (indicating reboot). Calculate average for multiple restart in range 24h, 7d, 30d | Python: scan ordered `uptime_s`, detect decreases |
| 13 | **Thermal Degradation Slope** | Linear regression slope of `gpu_temp_c` at constant utilization over given report range 24h, 7d, 30d | Python: filter rows where `gpu_util_pct` is within ±10% of median, then `np.polyfit` |
| 14 | **Cost per Active GPU-Hour** | `Sum(total_system_power_w) * interval / 3600 * rate / active_gpu_hours` | SQL + Python: reuse existing power aggregation, divide by active GPU count |
| 15 | **Idle Power Waste Cost** | Same as #18 but filtered to `has_active_job=False` periods | SQL + Python |



## 4. Detailed Computation Specifications

### 4.1 Cooling Efficiency Index
- **Definition:** `ΔGPU_Temp / ΔGPU_Power` (°C/W) — how much temperature rises per watt of power increase
- **Data Source:** `LatestSnapshot` (fetched as `prev_ls` before transaction) → `prev_ls.gpu_temps_json[0]`, `prev_ls.gpu_power_draws_json[0]`
- **Current Values:** `GPUMetric` being created: `gpu_temp_c`, `power_draw_w`
- **Time Range:** **2 consecutive snapshots** (1-minute interval)
- **Computation:** At ingest, point-in-time delta
- **Minimum Power Delta:** 1 W, using the same denominator handling as the Cooling Efficiency Index
- **Real Data Example:**
  - Previous: temp=72.0°C, power=350.0W
  - Current: temp=74.0°C, power=360.0W
  - Delta: 2.0°C / 10.0W = **0.2 °C/W**
- **Storage:** `GPUMetric.cooling_efficiency_index` (per-rig, per-minute)
- **Compaction:** `avg` at 15m/1h tiers

- **Interpretation:**
Lower values generally indicate a smaller temperature response for a given power change.
Higher values indicate a larger temperature response and may indicate weaker thermal response.
The metric is primarily useful for trend and anomaly detection, because GPU temperature is also affected by ambient temperature, fan speed, workload, and thermal inertia.
Negative values are possible when temperature decreases while power increases, or vice versa.

**Code (serializers.py):**
```python
MIN_POWER_DELTA_W = 1.0

prev_ls = LatestSnapshot.objects.filter(
    rig_uuid=rig_uuid
).first()

prev_gpu_temp = (
    prev_ls.gpu_temps_json[0]
    if prev_ls and prev_ls.gpu_temps_json
    else None
)

prev_gpu_power = (
    prev_ls.gpu_power_draws_json[0]
    if prev_ls and prev_ls.gpu_power_draws_json
    else None
)

curr_gpu_temp = curr_gpu.gpu_temp_c
curr_gpu_power = curr_gpu.power_draw_w

cooling_efficiency_index = None

if (
    prev_gpu_temp is not None
    and prev_gpu_power is not None
    and curr_gpu_temp is not None
    and curr_gpu_power is not None
):
    delta_temp = curr_gpu_temp - prev_gpu_temp
    delta_power = curr_gpu_power - prev_gpu_power

    if abs(delta_power) < MIN_POWER_DELTA_W:
        effective_delta_power = MIN_POWER_DELTA_W
    else:
        effective_delta_power = delta_power

    cooling_efficiency_index = (
        delta_temp / effective_delta_power
    )

```

---

### 4.2 Fan-Adjusted Cooling Response Index
- **Definition:** `(ΔTemp / effective ΔPower) / (1 + ΔFan% / 100)` (°C/W) — temperature response per watt of GPU power change, adjusted for the change in fan speed
- **Data Source:** `LatestSnapshot` → `prev_ls.gpu_fans_json[0]`, `prev_ls.gpu_temps_json[0]`, `prev_ls.gpu_power_draws_json[0]`
- **Current Values:** `GPUMetric` being created: `fan_speed_pct`, `gpu_temp_c`, `power_draw_w`
- **Time Range:** **2 consecutive snapshots** (1-minute interval)
- **Computation:** At ingest, point-in-time delta
- **Storage:** `GPUMetric.fan_adjusted_cooling_response` (per-rig, per-minute)
- **Compaction:** `avg` at 15m/1h tiers
- **Interpretation:** Lower values generally indicate a smaller temperature response relative to power change after accounting for fan-speed change. The metric is intended primarily for thermal trend and anomaly detection rather than as a physically exact cooling-efficiency measurement.

**Code (serializers.py):**
```python
MIN_POWER_DELTA_W = 1.0

prev_ls = LatestSnapshot.objects.filter(
    rig_uuid=rig_uuid
).first()

prev_gpu_temp = (
    prev_ls.gpu_temps_json[0]
    if prev_ls and prev_ls.gpu_temps_json
    else None
)

prev_gpu_power = (
    prev_ls.gpu_power_draws_json[0]
    if prev_ls and prev_ls.gpu_power_draws_json
    else None
)

prev_gpu_fan = (
    prev_ls.gpu_fans_json[0]
    if prev_ls and prev_ls.gpu_fans_json
    else None
)

curr_gpu_temp = curr_gpu.gpu_temp_c
curr_gpu_power = curr_gpu.power_draw_w
curr_gpu_fan = curr_gpu.fan_speed_pct

fan_adjusted_cooling_response = None

if (
    prev_gpu_temp is not None
    and prev_gpu_power is not None
    and prev_gpu_fan is not None
    and curr_gpu_temp is not None
    and curr_gpu_power is not None
    and curr_gpu_fan is not None
):
    delta_temp = curr_gpu_temp - prev_gpu_temp
    delta_power = curr_gpu_power - prev_gpu_power
    delta_fan = curr_gpu_fan - prev_gpu_fan

    if abs(delta_power) < MIN_POWER_DELTA_W:
        effective_delta_power = MIN_POWER_DELTA_W
    else:
        effective_delta_power = delta_power

    fan_adjustment = 1 + (delta_fan / 100.0)

    fan_adjusted_cooling_response = (
        (delta_temp / effective_delta_power)
        / fan_adjustment
    )
```

---

### 4.3 VRAM Bandwidth Saturation Index
- **Definition:** `mem_controller_util_pct / gpu_util_pct` The VRAM Bandwidth Saturation Index estimates how heavily the GPU's memory subsystem is being utilized relative to overall GPU utilization.
- **Data Source:** `LatestSnapshot` → `prev_ls.mem_controller_util_pct_json[0]`, `prev_ls.gpu_util_pct_json[0]` (verify in database model and correct those names, they can be incorrect)
- **Current Values:** `GPUMetric` being created: `mem_controller_util_pct`, `gpu_util_pct`, 
- **Time Range:** **2 consecutive snapshots** (1-minute interval)
- **Computation:** At ingest, point-in-time delta
- **Storage:** `GPUMetric.fan_adjusted_cooling_response` (per-rig, per-minute)
- **Compaction:** `avg` at 15m/1h tiers
- **Interpretation:** 
Index	Interpretation
< 0.5	Memory subsystem is relatively lightly utilized compared with GPU compute
0.5–1.0	Increasing memory pressure
≈ 1.0	Memory controller utilization is comparable to GPU utilization
> 1.0	Memory controller is more heavily utilized than overall GPU compute

**Code (serializers.py):**
```python
MIN_GPU_UTIL_PCT = 1.0
MIN_MEM_CONTROLLER_UTIL_PCT = 1.0

curr_gpu_util = curr_gpu.gpu_util_pct
curr_mem_controller_util = curr_gpu.mem_controller_util_pct

vram_bandwidth_saturation = None

if (
    curr_gpu_util is not None
    and curr_mem_controller_util is not None
):
    effective_gpu_util = curr_gpu_util
    effective_mem_controller_util = curr_mem_controller_util

    if (
        curr_gpu_util == 0
        and curr_mem_controller_util == 0
    ):
        effective_gpu_util = MIN_GPU_UTIL_PCT
        effective_mem_controller_util = MIN_MEM_CONTROLLER_UTIL_PCT

    vram_bandwidth_saturation = (
        effective_mem_controller_util / effective_gpu_util
    )
```

---

### 4.4 CPU-to-GPU Power Ratio
- **Definition:** TODO
- **Data Source:** TODO
- **Current Values:** TODO
- **Time Range:** **2 consecutive snapshots** (1-minute interval)
- **Computation:** At ingest, point-in-time delta
- **Storage:** TODO
- **Compaction:** `avg` at 15m/1h tiers
- **Interpretation:** TODO

## 5. Implementation Architecture

### 5.1 New Database Models (for pre-computed chart metrics)

```python
# gpu_monitor/metrics_app/models.py — ADD to existing file

class GPUMetric(models.Model):
    # ... existing fields ...
    
    # NEW: Pre-computed derived metrics (point-in-time)
    cooling_efficiency_index = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            '°C/W — temperature change per watt of GPU power change; '
            'higher values indicate a larger temperature response to power changes'
        ),
    )
    fan_adjusted_cooling_response = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            '°C/W — temperature change per watt of GPU power change, '
            'adjusted for the change in fan speed; higher values indicate a larger '
            'temperature response after accounting for fan-speed changes'
        ),
    )
    vram_bandwidth_saturation = models.FloatField(null=True, blank=True,
        help_text=(
            'Ratio of memory-controller utilization to GPU utilization; '
            'higher values indicate greater memory-bandwidth pressure relative '
            'to GPU compute utilization'
        )
    )
    cpu_to_gpu_power_ratio = models.FloatField(null=True, blank=True,
        help_text='cpu_power_w / sum(gpu_power_draw_w); high = CPU-bound workload')
```

**Ingest serializer update** (`serializers.py`): Compute these values in `process_ingest()` when GPU data is present, store in GPUMetric row.

**Compaction** (`compact_data.py`): Add these fields to `COMPACT_TABLES[0]['agg_fields']` with `'avg'` aggregation (they're ratios, average of ratios is acceptable).

**Chart registry** (`chart-registry.js`): Add new entries pointing to the new metric names.

### 5.2 Report Tab Extension (for windowed statistics)

Extend `_build_report_context()` in `dashboard/views.py` to compute the report-tab metrics:



**Template** (`_report_table.html`): Place calculated values in correct sections either per GPU or System section with 24h/7d/30d columns.

### 5.3 UI Integration

**Historical Charts tab:**  new chart cards added to `rig_detail.html` at the end.

**Report tab:**  table rows for each metric in correct sections, columns for 24h/7d/30d.


## 6. Priority & Phasing

### Phase 1: Pre-computed Charts (1-2 days)
1. Add fields to GPUMetric model + migration
2. Update serializer `process_ingest()` to compute them
3. Update `compact_data.py` to aggregate them
4. Add  chart registry entries +  chart cards in rig_detail.html
5. Test: charts appear, data flows, compaction works

### Phase 2: Report Tab Statistical Analysis (2-3 days)
1. Extend `_build_report_context()` with additional aggregations
2. Add Python post-processing functions for correlations, slopes, transitions
3. Extend `_report_table.html` with new data in either per GPU or System section
4. Add 24h/7d/30d columns (reuse existing range selector)
5. Test: numbers make sense, performance acceptable

### Phase 3: Polish (optional)
- Add "Analysis" subtab if Report is too crowded
- Add trend indicators (↑/↓) for degradation metrics
- Add tooltips explaining each metric

## 7. Key Technical Decisions


4. **Pearson correlation**: Use manual formula to avoid scipy dependency:
   ```python
   r = sum((x - x_mean) * (y - y_mean)) / sqrt(sum((x - x_mean)^2) * sum((y - y_mean)^2))
   ```

5. **Thermal Degradation Slope**: Filter rows where `gpu_util_pct` within ±10% of median utilization in window. Then linear regression on `gpu_temp_c` vs time. Slope in °C/day.

6. **Job State Transitions**: Scan ordered `has_active_job` values (0/1). Count `0→1` and `1→0` separately. Report both.

7. **Underutilization Duration**: Scan ordered `gpu_util_pct`. When `has_active_job=True` AND `gpu_util_pct < 5`, accumulate minutes. Reset on `gpu_util_pct >= 5` or `has_active_job=False`.

