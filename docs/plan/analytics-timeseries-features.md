# Analytics Timeseries Features — Analysis & Architecture Plan

## 1. Executive Summary

This document analyzes 21 proposed analytics metrics derived from GPU rig timeseries data, categorizes them by **where they belong** (Historical Charts subtab vs. Statistical Analysis report tab), and defines the **calculation architecture** — whether values are pre-computed at ingest time (stored in new DB models) or computed on-demand when the user opens the relevant tab.

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

### Mode B: On-Demand Calculation (for Report tab / Statistical Analysis)

**What it is:** When the user opens the Report tab (or a new Statistical Analysis subtab), the server runs SQL aggregation queries against the existing timeseries tables and computes derived metrics in Python. Results are cached at the view level (55s TTL, matching existing report caching pattern).

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

### Hybrid Approach (Recommended)

| Metric type | Calculation mode | Rationale |
|---|---|---|
| Single-point ratios/differences (Cooling Efficiency, Fan-to-Temp Gain, VRAM Saturation, CPU-to-GPU Power Ratio) | **Pre-compute at ingest** → store in GPUMetric or new AnalyticsMetric model | These are point-in-time values computable from one snapshot row. Charts can plot them directly like any other metric. |
| Windowed statistics (Thermal Degradation Slope, Clock Stability Index, Thermal Hysteresis, Temp-to-PowerLimit Stability) | **On-demand in Report tab** | These need rolling windows (7d, 30d) and regression. Not feasible at ingest. |
| Transition counts (Job State Transition Frequency) | **On-demand in Report tab** | Needs ordered sequence of `has_active_job` values across the time range. |
| Cumulative sums (Idle-to-Peak Delta, Idle Power Waste, Cost per GPU-Hour, Power-on Hours) | **On-demand in Report tab** | These are range aggregations (Sum/Max/Min over the period). |
| Correlations (Memory vs. Core Utilization) | **On-demand in Report tab** | Needs paired (mem_controller_util_pct, gpu_util_pct) values across the window. |
## 3. Metric Classification: Charts vs. Report Tab

### 3.1 Metrics That Belong in Historical Charts Subtab

These are **time-series visualizations** — they plot a derived value over time, just like existing charts (GPU Temp, Fan Speed, etc.). They follow the same pattern: fetch from ChartDataView, render with Chart.js.

| # | Metric | Chart Name | Data Source | Calculation | Chart Type |
|---|--------|-----------|-------------|-------------|------------|
| 1 | **Cooling Efficiency Index** | GPU Cooling Efficiency | GPUMetric | `ΔTemp / ΔPower` = `(gpu_temp_c - ambient) / power_draw_w` | Line, per-GPU |
| 2 | **Fan Speed-to-Temperature Gain** | Fan-to-Temp Gain | GPUMetric | `ΔFanSpeed% / ΔTemp°C` = derivative of fan response vs temp change | Line, per-GPU |
| 3 | **VRAM Bandwidth Saturation Index** | VRAM Bandwidth Saturation | GPUMetric | `mem_controller_util_pct / gpu_util_pct` | Line, per-GPU |
| 4 | **CPU-to-GPU Power Ratio** | CPU/GPU Power Ratio | GPUMetric + MetricSnapshot | `power_cpu_w / power_gpu_w` (or `cpu_power_w / sum(power_draw_w)`) | Line, single |
| 5 | **Fan Bearing Wear Indicator** | Fan Bearing Wear | GPUMetric | `fan_speed_pct / gpu_temp_c` at constant power_draw_w (requires filtering) | Line, per-GPU |

**Rationale for Chart placement:** These are **point-in-time derived metrics** — each data point is computed from a single snapshot row. They can be pre-computed at ingest time and stored as new fields on GPUMetric (or a new AnalyticsMetric model), then exposed through the existing ChartDataView → chart-registry pipeline with zero changes to the fetch/render flow.

### 3.2 Metrics That Belong in Statistical Analysis (Report Tab)

These are **aggregate statistics over a time window** (24h/7d/30d). They don't make sense as continuous time-series charts — they're summary numbers for a period. They belong in the Report tab as an additional section.

| # | Metric | Calculation Method | SQL/Python |
|---|--------|-------------------|-------------|
| 6 | **Thermal Hysteresis** | Temp difference between heating and cooling phases at same utilization | Python: pair rows by utilization bucket, compute ΔTemp |
| 7 | **Temperature-to-PowerLimit Ratio Stability** | Std dev of `power_limit_w` grouped by `gpu_temp_c` ranges | SQL: GROUP BY temp bucket, compute STDDEV(power_limit_w) |
| 8 | **Memory vs. Core Utilization Correlation** | Pearson r between `mem_controller_util_pct` and `gpu_util_pct` | Python: `scipy.stats.pearsonr` or manual formula |
| 9 | **Compute-to-Memory Ratio Trend** | Rolling average of `gpu_util_pct / mem_controller_util_pct` over time | Python: compute ratio per bucket, then linear regression slope |
| 10 | **Clock Stability Index** | Std dev of `gpu_core_clock_mhz` over rolling windows | SQL: STDDEV(gpu_core_clock_mhz) grouped by time window |
| 11 | **Frequency-to-PowerLimit Ratio Stability** | Std dev of `power_limit_w` grouped by `gpu_core_clock_mhz` ranges | SQL: GROUP BY clock bucket, STDDEV(power_limit_w) |
| 12 | **Idle-to-Peak Power Delta** | `Max(power_draw_w) - Min(power_draw_w)` over the period | SQL: `Max - Min` in the existing GPU aggregation query |
| 13 | **Idle Power Waste Ratio** | `Avg(power_draw_w when has_active_job=False) / Avg(power_draw_w when has_active_job=True)` | SQL: conditional aggregation |
| 14 | **Job State Transition Frequency** | Count of `has_active_job` state changes (0→1 and 1→0) | Python: ordered scan of `has_active_job` values |
| 15 | **Underutilization Duration** | Sum of continuous minutes where `gpu_util_pct < 5` AND `has_active_job=True` | Python: scan ordered rows, accumulate gaps |
| 16 | **Power-on Hours Before Restart** | Max `uptime_s` before a drop (indicating reboot) | Python: scan ordered `uptime_s`, detect decreases |
| 17 | **Thermal Degradation Slope** | Linear regression slope of `gpu_temp_c` at constant utilization over 30d | Python: filter rows where `gpu_util_pct` is within ±5% of median, then `np.polyfit` |
| 18 | **Cost per Active GPU-Hour** | `Sum(total_system_power_w) * interval / 3600 * rate / active_gpu_hours` | SQL + Python: reuse existing power aggregation, divide by active GPU count |
| 19 | **Idle Power Waste Cost** | Same as #18 but filtered to `has_active_job=False` periods | SQL + Python |

## 4. Direct Answers to Your Questions

### Q1: "Will those statistical analysis charts be included in Historical Charts subtab as new chats? Do we calculate them during ingest and serialization...?"

**Answer: Split by metric type.**

**YES — Pre-compute at ingest (Historical Charts subtab):**
- Cooling Efficiency Index (a)
- Fan Speed-to-Temperature Gain (b) 
- VRAM Bandwidth Saturation Index (f) — **NOT the same as correlation; see below**
- CPU-to-GPU Power Ratio (j)
- Fan Bearing Wear Indicator (k)

These are **point-in-time derived metrics**. Each data point = one snapshot row. Computed in `process_ingest()` serializer, stored in new fields on GPUMetric (or new AnalyticsMetric model), exposed via existing ChartDataView.

**NO — On-demand in Report/Statistical Analysis tab:**
- Thermal Hysteresis (c) — needs heating/cooling phase pairing across time
- Temperature-to-PowerLimit Ratio Stability (d) — needs grouped std dev across window
- Memory vs. Core Utilization Correlation (e) — needs paired values across window
- Compute-to-Memory Ratio Trend (g) — needs rolling ratio + regression slope
- Frequency-to-PowerLimit Ratio Stability (h) — needs grouped std dev
- Clock Stability Index (i) — needs rolling std dev

These are **windowed statistics**. They don't exist as a "value at time T" — they exist as "statistic over window W". Cannot be pre-computed at single-point ingest.

### Q2: "Will Statistical Analysis be new subtab after Historical Charts subtab? Calculate them only when user request it?"

**Yes.** Add a "Statistical Analysis" subtab (or section within Report tab) that:
1. Loads on-demand when user clicks the tab (HTMX, like Report tab)
2. Runs SQL aggregations + Python post-processing against existing timeseries tables
3. Caches results at view level (55s TTL, matching existing report pattern)
4. Shows 24h / 7d / 30d columns side-by-side (like Report tab)

This is **exactly the existing Report tab pattern** — extend `_build_report_context` with additional computed fields, render in template.

### Q3: "Memory vs. Core Utilization Correlation (e) vs VRAM Bandwidth Saturation Index (f) — same thing?"

**No, they are different:**

| Metric | Formula | Purpose |
|---|---|---|
| **Memory vs. Core Utilization Correlation (e)** | Pearson r(`mem_controller_util_pct`, `gpu_util_pct`) over time window | **Statistical relationship**: Are the two metrics correlated? r ≈ 1 = memory-bound; r ≈ 0 = independent; r < 0 = inverse relationship. Single number per time window. |
| **VRAM Bandwidth Saturation Index (f)** | `mem_controller_util_pct / gpu_util_pct` **per snapshot** | **Point-in-time ratio**: At this moment, how much memory bandwidth is used per unit of compute? > 1.0 = memory bottleneck right now; < 0.5 = compute bottleneck right now. Time-series chartable. |

**Placement:**
- (f) → **Historical Charts** (pre-computed per snapshot)
- (e) → **Statistical Analysis Report tab** (single number per 24h/7d/30d window)

## 5. Implementation Architecture

### 5.1 New Database Models (for pre-computed chart metrics)

```python
# gpu_monitor/metrics_app/models.py — ADD to existing file

class GPUMetric(models.Model):
    # ... existing fields ...
    
    # NEW: Pre-computed derived metrics (point-in-time)
    cooling_efficiency_index = models.FloatField(null=True, blank=True,
        help_text='(gpu_temp_c - ambient_estimate) / power_draw_w; higher = worse cooling')
    fan_to_temp_gain = models.FloatField(null=True, blank=True,
        help_text='Derivative: fan_speed_pct change per degree temp change')
    vram_bandwidth_saturation = models.FloatField(null=True, blank=True,
        help_text='mem_controller_util_pct / gpu_util_pct; >1.0 = memory bottleneck')
    cpu_to_gpu_power_ratio = models.FloatField(null=True, blank=True,
        help_text='cpu_power_w / sum(gpu_power_draw_w); high = CPU-bound workload')
    fan_bearing_wear_indicator = models.FloatField(null=True, blank=True,
        help_text='fan_speed_pct / gpu_temp_c at constant power; rising = bearing wear')
```

**Ingest serializer update** (`serializers.py`): Compute these 5 values in `process_ingest()` when GPU data is present, store in GPUMetric row.

**Compaction** (`compact_data.py`): Add these 5 fields to `COMPACT_TABLES[0]['agg_fields']` with `'avg'` aggregation (they're ratios, average of ratios is acceptable).

**Chart registry** (`chart-registry.js`): Add 5 new entries pointing to the new metric names.

### 5.2 Report Tab Extension (for windowed statistics)

Extend `_build_report_context()` in `dashboard/views.py` to compute the 14 report-tab metrics:

```python
# Additional aggregations in the existing GPU query (Query 1b):
gpu_agg = list(
    GPUMetric.objects.filter(**base_filter)
    .values('gpu_index', 'model')
    .annotate(
        # ... existing fields ...
        # NEW: For windowed stats
        gpu_temp_c_stddev=StdDev('gpu_temp_c'),
        gpu_core_clock_mhz_stddev=StdDev('gpu_core_clock_mhz'),
        power_limit_w_stddev=StdDev('power_limit_w'),
        power_draw_w_min=Min('power_draw_w'),
        power_draw_w_max=Max('power_draw_w'),
        # For correlation: need raw paired values (fetch separately)
    ).order_by('gpu_index')
)

# Additional MetricSnapshot aggregations:
snap_agg = MetricSnapshot.objects.filter(**base_filter).aggregate(
    # ... existing ...
    # NEW:
    cpu_power_w_min=Min('cpu_power_w'),
    cpu_power_w_max=Max('cpu_power_w'),
    total_system_power_w_min=Min('total_system_power_w'),
    total_system_power_w_max=Max('total_system_power_w'),
)

# Python post-processing (after queries):
# - Pearson correlation (mem_controller_util_pct, gpu_util_pct) per GPU
# - Thermal hysteresis (paired heating/cooling phases)
# - Thermal degradation slope (linear regression on temp@constant_util)
# - Job state transitions (scan has_active_job ordered)
# - Underutilization duration (scan gpu_util_pct < 5 with has_active_job=True)
# - Power-on hours before restart (scan uptime_s for drops)
# - Cost calculations (reuse power_total_kwh, divide by active GPU hours)
```

**Template** (`_report_table.html`): Add new "Statistical Analysis" section after System section with 24h/7d/30d columns.

### 5.3 UI Integration

**Historical Charts tab:** 5 new chart cards added to `rig_detail.html` (after GPU Power chart, before VRAM chart).

**Report tab:** New section "Statistical Analysis" with table rows for each metric, columns for 24h/7d/30d.

**New subtab (optional):** If Report tab gets too long, add a 6th tab "Analysis" between Charts and Containers. But extending Report is simpler first.

## 6. Priority & Phasing

### Phase 1: Pre-computed Charts (1-2 days)
1. Add 5 fields to GPUMetric model + migration
2. Update serializer `process_ingest()` to compute them
3. Update `compact_data.py` to aggregate them
4. Add 5 chart registry entries + 5 chart cards in rig_detail.html
5. Test: charts appear, data flows, compaction works

### Phase 2: Report Tab Statistical Analysis (2-3 days)
1. Extend `_build_report_context()` with additional aggregations
2. Add Python post-processing functions for correlations, slopes, transitions
3. Extend `_report_table.html` with Statistical Analysis section
4. Add 24h/7d/30d columns (reuse existing range selector)
5. Test: numbers make sense, performance acceptable

### Phase 3: Polish (optional)
- Add "Analysis" subtab if Report is too crowded
- Add trend indicators (↑/↓) for degradation metrics
- Add tooltips explaining each metric

## 7. Key Technical Decisions

1. **Ambient temperature estimate** for Cooling Efficiency: Use `cpu_temp_c` as proxy when GPU is idle, or a configurable per-rig ambient offset (default 25°C). Store in Rig model.

2. **Fan-to-Temp Gain derivative**: Compute as `(fan_speed_pct - prev_fan) / (gpu_temp_c - prev_temp)` using the previous snapshot for the same GPU. Requires access to previous row in serializer — doable via `LatestSnapshot.gpu_fans_json` / `gpu_temps_json`.

3. **Fan Bearing Wear Indicator**: Only meaningful when `power_draw_w` is stable (±5%). Filter in serializer: if `abs(power_draw_w - prev_power) > threshold`, set to NULL.

4. **Pearson correlation**: Use manual formula to avoid scipy dependency:
   ```python
   r = sum((x - x_mean) * (y - y_mean)) / sqrt(sum((x - x_mean)^2) * sum((y - y_mean)^2))
   ```

5. **Thermal Degradation Slope**: Filter rows where `gpu_util_pct` within ±5% of median utilization in window. Then linear regression on `gpu_temp_c` vs time. Slope in °C/day.

6. **Job State Transitions**: Scan ordered `has_active_job` values (0/1). Count `0→1` and `1→0` separately. Report both.

7. **Underutilization Duration**: Scan ordered `gpu_util_pct`. When `has_active_job=True` AND `gpu_util_pct < 5`, accumulate minutes. Reset on `gpu_util_pct >= 5` or `has_active_job=False`.

## 8. Summary Table

| Metric | Location | Calculation | Storage |
|---|---|---|---|
| Cooling Efficiency Index | Historical Charts | Per-snapshot: `(gpu_temp_c - ambient) / power_draw_w` | GPUMetric.cooling_efficiency_index |
| Fan Speed-to-Temp Gain | Historical Charts | Per-snapshot: derivative vs prev snapshot | GPUMetric.fan_to_temp_gain |
| VRAM Bandwidth Saturation | Historical Charts | Per-snapshot: `mem_controller_util_pct / gpu_util_pct` | GPUMetric.vram_bandwidth_saturation |
| CPU-to-GPU Power Ratio | Historical Charts | Per-snapshot: `cpu_power_w / sum(gpu_power_draw_w)` | GPUMetric.cpu_to_gpu_power_ratio |
| Fan Bearing Wear Indicator | Historical Charts | Per-snapshot: `fan_speed_pct / gpu_temp_c` (filtered) | GPUMetric.fan_bearing_wear_indicator |
| Thermal Hysteresis | Report: Statistical Analysis | Window: paired heating/cooling ΔTemp at same util | Computed on-demand |
| Temp-to-PowerLimit Stability | Report: Statistical Analysis | Window: STDDEV(power_limit_w) by temp buckets | Computed on-demand |
| Mem vs Core Correlation | Report: Statistical Analysis | Window: Pearson r(mem_controller, gpu_util) | Computed on-demand |
| Compute-to-Memory Ratio Trend | Report: Statistical Analysis | Window: regression slope of ratio over time | Computed on-demand |
| Clock Stability Index | Report: Statistical Analysis | Window: STDDEV(gpu_core_clock_mhz) | Computed on-demand |
| Freq-to-PowerLimit Stability | Report: Statistical Analysis | Window: STDDEV(power_limit_w) by clock buckets | Computed on-demand |
| Idle-to-Peak Power Delta | Report: Statistical Analysis | Window: Max(power) - Min(power) | Computed on-demand |
| Idle Power Waste Ratio | Report: Statistical Analysis | Window: Avg(power@idle) / Avg(power@active) | Computed on-demand |
| Job State Transition Frequency | Report: Statistical Analysis | Window: count of 0↔1 transitions in has_active_job | Computed on-demand |
| Underutilization Duration | Report: Statistical Analysis | Window: minutes(util<5 AND has_job=True) | Computed on-demand |
| Power-on Hours Before Restart | Report: Statistical Analysis | Window: max uptime_s before drop | Computed on-demand |
| Thermal Degradation Slope | Report: Statistical Analysis | Window: °C/day slope at constant utilization | Computed on-demand |
| Cost per Active GPU-Hour | Report: Statistical Analysis | Window: energy_cost / active_gpu_hours | Computed on-demand |
| Idle Power Waste Cost | Report: Statistical Analysis | Window: energy_cost during idle periods | Computed on-demand |

---

**Next Step:** Approve plan → create branch `feat/analytics-timeseries` → implement Phase 1 (pre-computed charts).
