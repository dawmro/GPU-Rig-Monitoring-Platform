# Implementation Plan — Add Job Saturation Status to Report (24h, 7d, 30d)

**Branch:** `plan/report-job-saturation-status` (new, off main — never push to main)  
**Status:** PLANNING — awaiting user approval

---

## 1. User Request — Parsed

> Create new branch to work on planning for adding job saturation status to report for 24h, 7d and 30d.  
> Job saturation = percentage of time the rig had an active GPU job (GPU process or running Docker container) during the report period.

The report currently shows: GPU metrics, CPU/Memory/Power, Disk, Network, System (energy, errors).  
We need to add: **Job Saturation %** — a single percentage per report range.

---

## 2. Current State — Confirmed from Source (main @ ec26d94)

### 2.1 Report Endpoint (`dashboard/views.py`)

- **`htmx_report_data(request, uuid)`** (line 703): HTMX endpoint rendering `_report_table.html`
- **Accepts**: `range_hours` ∈ {24, 168, 720} (24h, 7d, 30d)
- **Caching**: 55s TTL per rig+range (`report_{uuid}_{range_hours}`)
- **Context builder**: `_build_report_context(uuid, uuid_str, range_hours)` (line 734)

### 2.2 Report Context Builder — `_build_report_context`

**5 SQL queries** (all aggregated at SQL level):

| Query | Table | Purpose | Rows scanned (24h / 7d / 30d) |
|-------|-------|---------|-------------------------------|
| 1a | GPUMetric | Raw scan for GPU identity changes | 1440 / 10080 / 43200 |
| 1b | GPUMetric | GPU metrics aggregation (avg/max per GPU) | Same as 1a |
| 2 | MetricSnapshot | CPU/Memory/Power/Errors aggregation | 1440 / 10080 / 43200 |
| 3 | StorageMetric | Disk metrics per device | Pre-bucketed for 7d/30d |
| 4 | NetworkMetric | Network metrics per interface | Pre-bucketed for 7d/30d |

**Power calculation**: `power_total_kwh = (total_system_power_w_avg * range_hours) / 1000` — derived from Query 2, no separate query.

### 2.3 Job Status Data — Already Exists

| Component | Field | Type | Aggregation |
|-----------|-------|------|-------------|
| Agent (`agent/run.py` line 1545) | `has_active_job = bool(gpu_processes) or any(running docker)` | bool | Computed per heartbeat |
| `MetricSnapshot` | `has_active_job = BooleanField(default=False, null=True)` | bool | **Compacted** with `'max'` |
| `LatestSnapshot` | `has_active_job = BooleanField(default=False)` | bool | Denormalized latest |
| `ChartDataView.SNAPSHOT_METRICS` | Includes `'has_active_job'` | — | `Max(Cast(..., IntegerField()))` per bucket |
| `compact_data.py` MetricSnapshot | `'has_active_job': 'max'` | — | `MAX(CAST(has_active_job AS INTEGER))` |

**Key insight**: The chart already computes **per-bucket job status** (0 or 1) for 1-min / 15-min / 1-hour buckets matching the compaction tiers. The report needs a **single aggregate percentage** across all buckets in the range.

### 2.4 Report Template (`templates/dashboard/_report_table.html`)

- GPU section (per-GPU tables with avg/max)
- GPU Identity Changes section
- CPU / Memory / Disk / Network sections
- **System section** (line 323-357): Avg Power, Max Power, Total Energy (kWh), Cost Estimate, Total Errors

→ **Natural location for Job Saturation**: System section, after Total Errors.

---

## 3. Self-Contained Steps — Each Safe to Apply Independently

Every step is a separate commit that does NOT break existing functionality.

---

### Step A — Add Job Saturation to Report Context Builder (`dashboard/views.py`)

**File**: `gpu_monitor/dashboard/views.py` — function `_build_report_context` (line 734)

**Current imports** (line 760): `from django.db.models import Avg, Max, Min, Sum`
**Need to add**: `Cast` from `django.db.models.functions`, `IntegerField`

**Logic**: Reuse the MetricSnapshot aggregation (Query 2, lines 846-862) — add `has_active_job` aggregation alongside existing fields.

**Changes needed**:

1. **Add imports** at line 760:
```python
from django.db.models import Avg, Max, Min, Sum
from django.db.models.functions import Cast
from django.db.models.fields import IntegerField
```

2. **Add to `snap_agg` query** (after line 861):
```python
        has_active_job_avg=Avg(Cast('has_active_job', IntegerField())),
```

3. **Add to return dict** (after line 904, before `**snap_agg`):
```python
        'job_saturation_pct': round((snap_agg.get('has_active_job_avg') or 0) * 100, 1),
```

**Why this works for all tiers**:
- **24h (Tier 1, raw 1-min)**: `AVG(CAST(has_active_job AS INTEGER))` → fraction of minutes with job
- **7d (Tier 2, 15-min buckets)**: Stored value = `MAX(CAST(...) AS INTEGER)` per bucket (0 or 1). AVG of stored values = fraction of buckets with job
- **30d (Tier 3, 1-hour buckets)**: Same as Tier 2

**No new query** — uses the existing MetricSnapshot query.

---

### Step B — Add Job Saturation to Report Template (`_report_table.html`)

**File**: `gpu_monitor/templates/dashboard/_report_table.html` — System section (after line 355)

```html
<tr class="border-b border-gray-700/50">
    <td class="py-1.5 pr-3">Job Saturation</td>
    <td class="text-right py-1.5 px-2 font-semibold text-yellow-300">
        {{ job_saturation_pct|floatformat:1 }}%
    </td>
</tr>
```

**Styling**: Yellow/gold (matching chart color for active job), bold for emphasis.

---

### Step C — System Check Defense (`metrics_app/checks.py`)

**New check function** (following existing pattern `check_has_active_job_system_checks`):

```python
@register('metrics_app')
def check_report_job_saturation(app_configs, **kwargs):
    errors = []
    # Verify _build_report_context includes has_active_job_avg in snap_agg
    # Verify return dict includes job_saturation_pct
    # Verify template renders job_saturation_pct
    return errors
```

**Runs on**: `manage.py check`, `manage.py test` — catches regressions at CI time.

---

### Step D — Test Coverage (`tests/test_chart_logic.py`)

Add test function `test_report_job_saturation_calculation()` verifying:
- 24h range: `has_active_job_avg` derived correctly from raw data
- 7d range: Works with pre-bucketed 15-min data
- 30d range: Works with pre-bucketed 1-hour data
- Edge case: No data → 0%
- Edge case: All buckets active → 100%

---

## 4. Technical Details — How It Works

### 4.1 Aggregation Strategy (No New Queries)

| Range | Data Tier | Bucket Size | `has_active_job` Stored As | Report Aggregation |
|-------|-----------|-------------|----------------------------|-------------------|
| 24h | Tier 1 (raw) | 1-min | bool per minute | `AVG(CAST(has_active_job AS INTEGER))` |
| 7d | Tier 2 (compacted) | 15-min | `MAX(CAST(... AS INTEGER))` per 15-min bucket (0 or 1) | `AVG(has_active_job)` over buckets |
| 30d | Tier 3 (compacted) | 1-hour | `MAX(CAST(... AS INTEGER))` per 1-hour bucket (0 or 1) | `AVG(has_active_job)` over buckets |

**Why AVG works for all tiers**:
- Tier 1: Raw bool → CAST to int (0/1) → AVG = fraction of minutes with job
- Tier 2/3: Already `MAX(bool)` per bucket (0 or 1) → AVG = fraction of buckets with job

**Consistent semantics**: "Percentage of time buckets in the range where at least one job was active."

### 4.2 Compaction Compatibility

`compact_data.py` already has:
```python
'has_active_job': 'max',  # line 122
```
With SQL generation defense (line 214-215):
```python
if agg == 'max' and f == 'has_active_job':
    select_parts.append(f"MAX(CAST({f} AS INTEGER)) AS {f}")
```
And insert cast-back (line 235-236):
```python
if col == 'has_active_job':
    return f"CAST({col} AS BOOLEAN)"
```

→ **No compaction changes needed** — the field is already correctly handled.

### 4.3 Cleanup Compatibility

`cleanup_old_data.py` deletes rows by timestamp — column-agnostic. No changes needed.

---

## 5. Files to Modify

| File | Change Type | Risk |
|------|-------------|------|
| `gpu_monitor/dashboard/views.py` | Add `has_active_job_avg` to `snap_agg`, compute `job_saturation_pct` | Low — single query, existing pattern |
| `gpu_monitor/templates/dashboard/_report_table.html` | Add row in System section | Zero — template only |
| `gpu_monitor/metrics_app/checks.py` | Add `check_report_job_saturation` | Zero — check only |
| `tests/test_chart_logic.py` | Add `test_report_job_saturation_calculation` | Zero — test only |

---

## 6. Acceptance Criteria

1. **Report displays Job Saturation %** for 24h, 7d, 30d ranges in System section
2. **Values are correct**: 
   - If rig ran jobs for 6 of 24 hours → 25%
   - If rig ran jobs for 12 of 168 hours → ~7%
   - No data → 0%
3. **No new DB queries** — uses existing MetricSnapshot aggregation
4. **`manage.py check` passes** with new system check
5. **Tests pass** including new test case
6. **No regression** in existing report metrics (GPU, CPU, Disk, Network, Power, Errors)

---

## 7. Future-Proofing Notes

### 7.1 If Detailed Job History Is Needed Later

As documented in `docs/archive/PLAN_chart_job_status.md` §4, a separate `JobStateMetric` or `JobEvent` model could be added for:
- What Docker image ran
- What GPU processes ran
- Job type (mining, training, inference, jupyter, etc.)
- Multiple concurrent jobs

**Current plan does NOT block this** — `MetricSnapshot.has_active_job` remains the fast-path boolean for saturation charts/reports. Future models would be additive.

### 7.2 If Per-GPU Job Saturation Is Needed

Current `has_active_job` is rig-level. Per-GPU would require:
- New field in `GPUMetric` (e.g., `has_active_process`)
- Agent computes per-GPU from `gpu_processes_json`
- Similar compaction/chart/report pipeline

**Not in scope** — current request is rig-level saturation.

---

## 8. Action Items — Before Implementation (User Approval Required)

Per workflow: **NEW BRANCH FIRST** → plan → approval → implement on branch → push to branch → user merges on GitHub.

This document IS the plan. Once approved:

1. Create feature branch from this plan branch: `feat/report-job-saturation-status`
2. Apply Step A (views.py)
3. Apply Step B (template)
4. Apply Step C (system check)
5. Apply Step D (test)
6. Run `manage.py check` and `manage.py test` to verify
7. Push to `feat/report-job-saturation-status`
8. User verifies and merges on GitHub — **never push to main, never merge branches**

---

## 9. References Verified

- `gpu_monitor/dashboard/views.py` — `_build_report_context`, `htmx_report_data`
- `gpu_monitor/templates/dashboard/_report_table.html` — System section
- `gpu_monitor/metrics_app/models.py` — `MetricSnapshot.has_active_job`, `LatestSnapshot.has_active_job`
- `gpu_monitor/metrics_app/views.py` — `ChartDataView.SNAPSHOT_METRICS`, `_handle_snapshot_metric` for `has_active_job`
- `gpu_monitor/metrics_app/management/commands/compact_data.py` — `COMPACT_TABLES` MetricSnapshot entry with `'has_active_job': 'max'`
- `gpu_monitor/metrics_app/checks.py` — `check_has_active_job_system_checks` (pattern for new check)
- `agent/run.py` — `has_active_job` computation
- `tests/test_chart_logic.py` — existing report tests pattern

*Plan written against main @ ec26d94. All references verified from source files in workspace.*