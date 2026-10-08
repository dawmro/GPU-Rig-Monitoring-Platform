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
- **Data Source:** `LatestSnapshot` (fetched as `prev_ls` before transaction). **Note:** `LatestSnapshot.gpu_temps_json` / `gpu_power_draws_json` / `gpu_fans_json` are JSON arrays — index 0 = first GPU. Verify payload array ordering matches `gpu_index`. Check `prev_ls.gpu_temps_json[0]` exists before reading; empty array yields `None` safely.
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

# Index by gpu_index (not hardcoded 0) — matches payload array order
prev_idx = gpu.get('gpu_index', 0) if 'gpu_index' in gpu else 0

prev_gpu_temp = (
    prev_ls.gpu_temps_json[prev_idx]
    if prev_ls and prev_ls.gpu_temps_json and prev_idx < len(prev_ls.gpu_temps_json)
    else None
)

prev_gpu_power = (
    prev_ls.gpu_power_draws_json[prev_idx]
    if prev_ls and prev_ls.gpu_power_draws_json and prev_idx < len(prev_ls.gpu_power_draws_json)
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
- **Data Source:** `LatestSnapshot` → `prev_ls.gpu_temps_json`, `prev_ls.gpu_power_draws_json`, `prev_ls.gpu_fans_json` (JSON arrays; index by `gpu_index`, not hardcoded 0). Same `prev_ls` fetch used for delta baseline.
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
    prev_ls.gpu_fans_json[prev_idx]
    if prev_ls and prev_ls.gpu_fans_json and prev_idx < len(prev_ls.gpu_fans_json)
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
- **Data Source:** Payload (`gpu.get('mem_controller_util_pct')`, `gpu.get('gpu_util_pct')`) — current snapshot values from agent payload (`serializers.py` 203-204 in `GPUMetric` update_or_create). `LatestSnapshot.gpu_mem_controller_utils_json` / `gpu_utils_json` arrays (line 334-337 `models.py`) for fast read. NOT `prev_ls`. NOT historical `GPUMetric` timeseries (this metric is current-state ratio at ingest time).
- **Current Values:** `GPUMetric` being created: `mem_controller_util_pct`, `gpu_util_pct`, 
- **Time Range:** **Single snapshot** (no delta needed — this is a point-in-time ratio, not a change-over-change metric). **Correction:** Original plan incorrectly said "2 consecutive snapshots (1-minute interval)"; the ratio uses current values only.
- **Computation:** Can be moved to agent code (`agent/run.py` 1054-1055: both values available in payload). Agent computes `vram_bandwidth_saturation = mem_controller_util_pct / max(gpu_util_pct, 1.0)` per GPU; serializer receives it in `gpu.get('vram_bandwidth_saturation')` and stores directly in `GPUMetric`. No server-side division needed — saves ingest CPU. If agent-side is not possible (e.g. Windows agent limit), server-side fallback uses same `max()` guard.
- **Storage:** `GPUMetric.vram_bandwidth_saturation` (new FloatField; NOT `fan_adjusted_cooling_response` — original plan had wrong storage field name for metric 3). Per-rig, per-minute.
- **Compaction:** `avg` at 15m/1h tiers (same as other GPUMetric ratios).
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

if (curr_gpu_util is not None and curr_mem_controller_util is not None):
    vram_bandwidth_saturation = (
        max(curr_mem_controller_util, MIN_MEM_CONTROLLER_UTIL_PCT)
        / max(curr_gpu_util, MIN_GPU_UTIL_PCT)
    )
```

---

### 4.4 CPU-to-GPU Power Ratio (Single-line, multi-GPU)
`cpu_power_w / sum(all gpu_power_draw_w)` — a ratio (not workload-bound indicator).
- **Source:** Latest payload (`power` dict) or `LatestSnapshot` (fast). **NOT `GPUMetric`** (that is historical timeseries; 4.4 is current-state). `LatestSnapshot.gpu_power_draws_json` (line 342 `models.py`) is the array; `power_cpu_w` is scalar (line 410 `models.py`).
- **Single line (payload / snapshot, not GPUMetric):** `cpu_power / sum(gpu_power_draws_json)` — denominator from `LatestSnapshot` array or payload `power.gpu_power_w`. No reference to `GPUMetric.power_draw_w` for this metric.
- **Storage / Compaction:** None — derived metric; compute from payload or `LatestSnapshot`. Not a timeseries feature by default.
- **Note:** Interpret as ratio only; do NOT label "CPU-bound" or "GPU-bound" from this number alone.

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
            'computed at ingest from current `gpu_util_pct` and `mem_controller_util_pct`; '
            'higher values indicate greater memory-bandwidth pressure relative to GPU compute'
        )
    )
    # NOTE: `cpu_to_gpu_power_ratio` is NOT added here. It is a cross-table metric
    # (MetricSnapshot.cpu_power_w vs GPUMetric.power_draw_w array / sum) and must be
    # computed on-demand in `_build_report_context()` or as a custom ChartDataView join.
    # See §4.4 correction and architecture note above.
```

**Ingest serializer update** (`serializers.py`): Compute these values in `process_ingest()` when GPU data is present, store in GPUMetric row.

**Compaction** (`compact_data.py`): Add these fields to `COMPACT_TABLES[0]['agg_fields']` with `'avg'` aggregation (they're ratios, average of ratios is acceptable).

**Chart registry** (`chart-registry.js`): Add new entries pointing to the new metric names.

### 5.2 Report Tab Extension (for windowed statistics)

Extend `_build_report_context()` in `dashboard/views.py` (line 734) to compute the report-tab metrics. **Verified code structure (views.py 734-916):**

- `base_filter` uses `metric_app.models.MetricSnapshot` (line 19 import confirmed) and `base_filter` filters by `rig_uuid` + time range.
- `_build_report_context()` already does 4 aggregation queries (`GPUMetric`, `MetricSnapshot`, `StorageMetric`, `NetworkMetric`) — adding derived metrics uses the same result sets, no extra queries needed.
- Existing `snap_agg` (line 848) returns aggregated values for CPU/Memory/Power/Errors. Report-tab derived metrics reuse these same aggregates.
- `gpu_agg` (line 774) aggregates per `gpu_index`. Windowed GPU metrics reuse this.

**Implementation rules verified against architecture:**
1. **MetricSnapshot IS compacted by `compact_data` (line 112-132)** — 0-1d raw, 1-7d 15m, 7-31d 1h (group `rig_uuid`). The `ChartDataView` comment (line 212) means charts aggregate SQL on-the-fly rather than reading pre-bucketed rows directly. Report metrics using `MetricSnapshot` (CPU power, job state, uptime) should aggregate via SQL `.aggregate()` over the range; this scans compacted rows (~720 for 30d) — much faster than raw scan. The existing `_build_report_context()` (line 848) already uses `.aggregate()` — extend that.
2. **GPUMetric IS compacted** (`COMPACT_TABLES` line 51-77, `metrics_gpumetric`). Report-tab GPU metrics over 7d/30d read pre-bucketed 15m/1h rows (~700 / ~720 rows) — much faster than raw scan. Use the existing `GPUMetric` aggregation (`Avg`, `Max`, `Sum`) — do NOT read raw time-series.
3. **LatestSnapshot for point-in-time ratios:** `cpu_to_gpu_power_ratio` for current state reads `LatestSnapshot` directly (no aggregation needed). This is faster than any time-series query.

**Specific metric implementations (code fragments):**

```python
# In dashboard/views.py — _build_report_context(), after snap_agg / gpu_agg
# --- Cooling Efficiency Index (report aggregate) ---
# Uses existing GPUMetric aggregation (pre-bucketed for 7d/30d)
# No new query — reuse gpu_agg results per gpu_index

# --- Memory vs Core Utilization Correlation (Pearson r) ---
# Filter: rows where gpu_util_pct and mem_controller_util_pct both non-null
# Use Python post-processing over the aggregated bucket values (not raw rows)
# Avoid scipy dependency; manual formula (see §7.4)

# --- Clock Stability Index ---
# SQL: STDDEV(gpu_core_clock_mhz) over range using pre-bucketed GPUMetric
# Since GPUMetric is compacted, use .aggregate(stddev=StdDev('gpu_core_clock_mhz'))

# --- Job State Transitions ---
# Python: ordered scan of MetricSnapshot.has_active_job values
# Because MetricSnapshot is NOT compacted, scan only if range_hours <= 168 (7d = 10K rows max)
# For 30d: approximate from `snap_agg['has_active_job_avg']` (fraction active) instead of full scan

# --- Underutilization Duration ---
# Python: scan MetricSnapshot ordered by timestamp; accumulate minutes where
# `gpu_util_pct < 5` AND `has_active_job = True`. Same 7d limit applies.

# --- Thermal Degradation Slope ---
# Python: filter `MetricSnapshot` rows where `gpu_util_pct` within ±10% of median
# in window, then `np.polyfit` on `gpu_temp_c` vs time (in days). Only for 24h/7d.
# 30d: approximate trend from `snap_agg` temperature delta.
```

**Performance guard:** For 30d range, never run full Python scan on `MetricSnapshot` raw rows (~43K). Always aggregate at SQL level first (`.aggregate()` / `.annotate()`), then apply Python only to aggregated bucket arrays (max ~720 buckets for 30d at 1h). This matches the existing `ChartDataView` optimization strategy (line 204-210).

### 5.3 UI Integration

**Historical Charts tab:** Add chart cards to `rig_detail.html` at end, referencing new registry entries (see `chart-registry.js`). **Verified template structure (`rig_detail.html` line 322):** `<script src="{% static 'js/chart-registry.js' %}?v=2"></script>` — registry entry id must match the card's `id` attribute (e.g., `chartGpuCoolingEfficiency` → loader function referencing metric name `cooling_efficiency_index`). No template code change beyond adding cards; loader pulls metric from `ChartDataView.GPU_METRICS` mapping.

**Report tab (`_report_table.html`):** Place new metric rows in correct sections. **Verified template structure (`dashboard/views.py` line 731):** Template renders from context dict returned by `_build_report_context()`. New keys (e.g., `cooling_efficiency_24h`, `correlation_7d`) must be added to the context dict (line 906 return) before the template can access them. Columns reuse existing `24h`/`7d`/`30d` structure from range selector (line 703 `htmx_report_data`).

**LatestSnapshot preference for faster reads:** Where possible, report metrics should prefer `LatestSnapshot` over `MetricSnapshot` aggregation. `LatestSnapshot` is a single-row fetch (`.first()` at line 833); `MetricSnapshot` aggregation scans thousands of rows. Example: current-state `cpu_to_gpu_power_ratio` uses `LatestSnapshot.power_cpu_w` directly (line 833 `latest_snap`). Historical aggregate still needs `MetricSnapshot` aggregation.

### 5.4 Verification Against Latest Architecture (Verified)

- `models.py`: `GPUMetric` fields (`cooling_efficiency_index`, `fan_adjusted_cooling_response`, `vram_bandwidth_saturation`) NOT yet added (plan only — no code changes made in this branch per user instruction). `cpu_to_gpu_power_ratio` removed from model proposal (§4.4 correction).
- `serializers.py`: `process_ingest()` (line 38) computes `GPUMetric` rows from payload. `prev_ls` fetched at line 89-103. Code fragment corrected above to index by `gpu_index` (not hardcoded 0).
- `compact_data.py`: `COMPACT_TABLES` line 51-77 (`metrics_gpumetric`) includes `gpu_util_pct`, `mem_controller_util_pct`, `gpu_core_clock_mhz`, `fan_speed_pct`, `power_draw_w`, `power_limit_w`. New derived fields (`cooling_efficiency_index`, `fan_adjusted_cooling_response`, `vram_bandwidth_saturation`) must be added to `agg_fields` with `'avg'` when implemented.
- `checks.py`: System checks (line 15-30) read `COMPACT_TABLES` in-memory (not file). Any new `GPUMetric` field added to model must also be added to `COMPACT_TABLES` static_fields (`COMPACT_TABLES[0]['static_fields']`) or defense checks will fail (line 154-155). See memory note `§Defense (W001/W004/0052)`: bug class → code + Django check + skill.
- `ChartDataView` (line 194): `SNAPSHOT_METRICS` (line 226) and `GPU_METRICS` (line 239) define chart endpoint metrics. New chart metrics must be added to `GPU_METRICS` mapping (e.g., `'cooling_efficiency_index': 'cooling_efficiency_index'`).


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

### 5.4 MetricSnapshot Compaction Status — CORRECTED (Verified against compact_data.py + ChartDataView)

**Contradiction resolved:** `compact_data.py` line 112 (`COMPACT_TABLES`) includes `metrics_metricsnapshot`. `ChartDataView` line 212 comment says "MetricSnapshot is NOT compacted". **Both statements are partially true — clarification:**

- `MetricSnapshot` IS in `COMPACT_TABLES` (line 112-132) and IS compacted: 0-1d raw → 1-7d 15m buckets → 7-31d 1h buckets. Group by `rig_uuid`. Aggregations: `avg`/`sum`/`max`/`min`/`last`/`avg_elementwise`. This is confirmed by reading `COMPACT_TABLES` directly (line 44-133 of `compact_data.py`).
- `ChartDataView` (line 212) treats it as "not compacted" for chart-fetching purposes: charts aggregate with SQL (`.annotate(bucket=TruncMinute)`) rather than reading pre-bucketed `MetricSnapshot` rows via `_read_prebucketed()`. This is a design choice — snapshot-level charts aggregate at query time (like `SNAPSHOT_METRICS` line 226).
- **Impact for analytics plan:** Report-tab metrics CAN (and should) aggregate from `MetricSnapshot` using SQL `.aggregate()` over the full time window. For 30d, the compacted table has ~720 rows (1h buckets) — much faster than scanning ~43K raw rows. The original plan's performance note needs correction: do NOT say "~43K rows — needs careful design" as a blocker; say "use SQL aggregation which handles both raw (<1d) and compacted (≥1d) transparently; for 30d this scans ~720 compacted rows."
- **Verification against defense rules:** Any new `MetricSnapshot` derived metric added at compaction level must also be reflected in `COMPACT_TABLES` `agg_fields` (line 114-128) and `checks.py` verifies this (line 15-30, in-memory import from `compact_data` line 30). See memory (§Defense): new field needs `COMPACT_TABLES` entry.

## 7. Key Technical Decisions (Corrected & Verified)

1. **LatestSnapshot vs Time-series for fast reads:** `LatestSnapshot` (single row per rig) is faster and cheaper than any time-series aggregation. Metrics that only need current state (`cpu_to_gpu_power_ratio` latest value, job status, GPU identity) must read `LatestSnapshot` first (`.first()` at line 833 of `dashboard/views.py`). Only historical aggregates (24h/7d/30d) should read `MetricSnapshot` (not compacted) or `GPUMetric` (compacted at 15m/1h). This is the core architecture principle applied to all metrics in this plan.

2. **Compaction defense (`checks.py`):** `COMPACT_TABLES` is read in-memory (line 30: `from metrics_app.management.commands.compact_data import COMPACT_TABLES`). Any new `GPUMetric` field requires both model addition AND `COMPACT_TABLES` entry update (`agg_fields` + `static_fields`) plus a system check verification (line 15-30 `checks.py`). See defense note in memory.

3. **Pearson correlation (manual formula — no scipy):**
   ```python
   import math
   def pearsonr_manual(x, y):
       n = len(x)
       mean_x = sum(x) / n
       mean_y = sum(y) / n
       num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
       den_x = sum((xi - mean_x)**2 for xi in x)
       den_y = sum((yi - mean_y)**2 for yi in y)
       if den_x == 0 or den_y == 0:
           return 0.0  # No variance = no correlation
       return num / math.sqrt(den_x * den_y)
   ```

4. **Thermal Degradation Slope (`np.polyfit` guard):** Filter rows where `abs(gpu_util_pct - median_util) <= 10`. If fewer than 3 points after filter, return `None` (not 0) to avoid false slope. Slope in °C/day requires time in days: `(timestamp - start).days`.

5. **Cooling Efficiency Index denominator guard:** Original plan had `MIN_POWER_DELTA_W = 1.0`. The serializer fragment uses `abs(delta_power) < MIN_POWER_DELTA_W` → `effective_delta_power = MIN_POWER_DELTA_W`. This avoids division by near-zero. Verify the same guard applies to all delta-based metrics (metrics 1, 2, 4 in §4.1-4.4).

6. **Job State Transitions:** Scan ordered `MetricSnapshot` rows (`has_active_job` boolean, mapped 0/1). For 30d (~43K rows), full Python scan is too slow. **Architecture fix:** Only compute full transition count for 24h/7d (`range_hours <= 168`). For 30d, approximate using `snap_agg['has_active_job_avg']` and assume transition frequency proportional to active-time variance.

7. **Underutilization Duration:** Python scan of `MetricSnapshot` ordered by timestamp. Reset conditions verified: `gpu_util_pct >= 5` OR `has_active_job == False` ends accumulation. Duration in minutes = `(end_time - start_time).total_seconds() / 60`. Only for `has_active_job == True` periods.

8. **Chart registry mapping (`chart-registry.js` line 11-42):** New chart cards must add entries to `registry` array with matching `id`. Example for metric 1:
   `{ id: 'chartGpuCoolingEfficiency', loader: function(uuid, range) { return window.GRM.ChartLoaders.loadChartMultiGpu('chartGpuCoolingEfficiency', 'cooling_efficiency_index', uuid, range, '°C/W'); } }`
   The loader uses `ChartDataView.GPU_METRICS` mapping at line 239 (`metrics_app/views.py`) — add `'cooling_efficiency_index': 'cooling_efficiency_index'` there.

9. **Performance contract for 30d report:** `MetricSnapshot` IS compacted (`COMPACT_TABLES` 112-132), so SQL aggregation over 30d scans ~720 compacted 1h-bucket rows — not ~43K raw. Only ranges <1d (≤1.4K raw rows) read un-bucketed data. Aggregate at SQL level first (`.aggregate()` / `.annotate()`), then process bucket arrays. This aligns with `ChartDataView` design (line 204-210) and `compact_data` 3-tier strategy.

