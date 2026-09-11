# Implementation Plan — Chart: Job Status (Active / Not Active)
Branch: `plan/chart-job-status` (new, off main — never push to main)
Status: PLANNING — pending approval before any implementation

---

## 1. User Request — Parsed

> Create new branch to work on planning for creating chart of Job status for rig: active or not.
> `has_active_job` is a bool true or false, we can map it to range 0 1 and calculate avg from that in a given time bucket: 1m, 15m or 1h, if there was a job.
> For now adding `has_active_job` to `MetricSnapshot` (new column, writes every heartbeat) is correct choice since it is consistent with rest of collected parameters for timeseries data. But think what will happen if we would want to have more detailed separate timeseries model for job-state history. Where we will have not only 0 1 job state for a given time point, but also docker image that ran at this time, gpu processes, or in a future job type (for example mining, video generation, image generation, jupyter notebook, model training and so on). Think about it and show your findings on how to update plan to be ready for those future features.

---

## 2. Current State — Confirmed from Source (main @ 8565769)

### 2.1 Payload (agent/run.py, agent_windows/run.py)
- `build_payload()` already computes `has_active_job = bool(gpu_processes) or any(running docker container)` (line 1244-1247).
- Payload sends `'has_active_job': has_active_job` at root level (line 1271).
- Schema version currently `1.14` (agent version `1.9.0`).

### 2.2 Serializer (metrics_app/serializers.py)
- `IngestSerializer` accepts `has_active_job = serializers.BooleanField(required=False, default=False)` (line 30).
- `validated.get('has_active_job', False)` is written to `LatestSnapshot.has_active_job` (line 533) — but **NOT yet** to `MetricSnapshot`.
- The `defaults` dict for MetricSnapshot (line 105-123) does **not** include `has_active_job`. This is the gap.

### 2.3 Database Model (metrics_app/models.py)
- `MetricSnapshot` (time-series, 1 row/heartbeat): **no** `has_active_job` field yet.
- `LatestSnapshot` (denormalized latest): `has_active_job = BooleanField(default=False)` exists (line 325).
- `LatestDockerContainer`: `image`, `status`, `container_id`, `name`, `manifest_json`, `logs_json` — latest only (delete-before-insert). No historical timeseries.
- `RigStatusEvent`: status transition events (online/stale/offline) — independent table, event-based.

### 2.4 Chart View (metrics_app/views.py — ChartDataView)
- `SNAPSHOT_METRICS` frozenset (line 225-230): defines which metrics are read from `MetricSnapshot` with on-the-fly AVG aggregation.
- If `has_active_job` is added to MetricSnapshot, it must also be added to `SNAPSHOT_METRICS` (or a new set) so ChartDataView knows how to aggregate it.
- For bool → 0/1 mapping: AVG of 0/1 over a bucket gives the fraction of time with active job = exactly what the user wants.
- Bucket minutes: 1 (≤24h), 15 (≤168h), 60 (>168h) — already handled by `_bucket_minutes_for_range()`.

### 2.5 Compaction (compact_data.py)
- `MetricSnapshot` IS compacted (`compact_data.py` line 104-119: included in `COMPACT_TABLES`, aggregated to 15m/1h buckets with `avg`/`sum`/`max`; FK-safe exclusion for referenced child rows). It is also cleaned (`cleanup_old_data.py` line 34). Adding `has_active_job` requires adding `has_active_job: 'max'` to the `agg_fields` in `compact_data.py` for MetricSnapshot (line 107-115), since it will be aggregated as `AVG` (bool 0/1 → fraction active). Cleanup needs no change (row-level delete by timestamp).
- This is critical: MetricSnapshot IS compacted (`compact_data.py` line 104-119: `agg_fields` includes `cpu_utilization_pct: 'avg'`, `cpu_temp_c: 'avg'`, etc.). Any new MetricSnapshot field **must** be added to that `agg_fields` dict or it will be lost during tier-2 (15m) / tier-3 (1h) compaction. For `has_active_job`: add `'has_active_job': 'max'` (bool -> MAX(0/1) = any active job in bucket (True/False)). ChartDataView aggregates it with `Avg` natively.
- If we later introduce a separate job-state timeseries table, it WOULD need its own compaction entry in `COMPACT_TABLES`.

### 2.6 Cleanup (cleanup_old_data.py)
- Deletes `metrics_metricsnapshot` by timestamp (>31d, batch 10K). Any new column is preserved automatically since cleanup operates on rows, not columns.
- No changes needed for `has_active_job` on MetricSnapshot.

---

## 3. Self-Contained Steps — Each Safe to Apply Independently

Every step is a separate commit that does NOT break existing functionality. If any step fails, revert that single commit; previous steps remain intact.

---

### Step A — Migration Only (DB schema change, zero code logic)
Creates `metrics_app/migrations/0051_metric_snapshot_has_active_job.py`.
- Adds `has_active_job = BooleanField(default=False, null=True)` to `MetricSnapshot`.
- Existing payloads without `has_active_job` default to `False` (backward compatible).
- No serializer/view changes yet → no behavior change; DB column exists but is empty until Step B writes to it.

### Step B — Serializer Writes to MetricSnapshot (data flow, no UI change)
Edits `metrics_app/serializers.py` only.
- Adds `'has_active_job': validated.get('has_active_job', False)` to the `MetricSnapshot` `defaults` dict (line 105-123).
- Keeps existing `LatestSnapshot` write unchanged.
- From now on, every heartbeat writes `has_active_job` to MetricSnapshot; existing charts unaffected (new field not yet queried).

### Step C — Compaction Includes the New Field (prevents data loss)
Edits `gpu_monitor/metrics_app/management/commands/compact_data.py` only.
- Adds `'has_active_job': 'avg'` to MetricSnapshot `agg_fields` (line 107-115).
- Without this: tier-2 (15m) and tier-3 (1h) compaction would drop the column from aggregated rows.
- Safe independently: only affects future compaction runs; does not touch raw data or existing queries.

### Step D — ChartDataView Queries the New Metric (UI feature activated)
Edits `gpu_monitor/metrics_app/views.py` only.
- Adds `'has_active_job'` to `SNAPSHOT_METRICS`.
- In `_handle_snapshot_metric()`: treats it as `AVG` (bool → 0.0/1.0 float), no byte conversion.
- Existing chart endpoints for other metrics unchanged.
- Only activates when front-end requests metric=`has_active_job`; existing templates ignore it until a chart loader is added.

### Step E — System Check (defense layer, runs on `manage.py check`)
New file `metrics_app/checks.py` (or `dashboard/checks.py`).
- Verifies: `MetricSnapshot` has field `has_active_job`; `SNAPSHOT_METRICS` contains it; serializer `defaults` writes it.
- Fails `manage.py check` if any of these are removed by a future commit (W001 defense).
- Zero runtime impact unless a regression is introduced.

### Step F — Skill Update (documentation layer, optional but recommended)
Patches relevant skill file (e.g., `skills/` if a chart-ingest skill exists).
- Documents: bool→float→AVG mapping; compaction requirement (`'avg'` in `agg_fields`); system check coverage.
- No code execution impact.

---

### Chart Integration Analysis — How Job Status Fits Existing Chart System

**Data flow for any historical chart (e.g., `cpu_utilization_pct`):**
1. Template (`rig_detail.html`) → `{% include "partials/_chart_card.html" with canvas_id="chartCpuUtil" title="CPU Utilization" %}`
2. Chart loader (`chart-runtime.js`) → `loadChart('chartCpuUtil', 'cpu_utilization_pct', uuid, range, '%', ...)`
3. `chart-loaders.js` → builds URL via `Base.buildChartUrl(uuid, range, { metric: 'cpu_utilization_pct' })`
4. URL hits `ChartDataView.get()` (`metrics_app/views.py`) → `SNAPSHOT_METRICS` dispatch → SQL `Avg` aggregation on `MetricSnapshot` → JSON response (`{labels, datasets}`)
5. `chart-loaders.js` renders with Chart.js (`line` type, color from `chart-colors.js`, options from `chart-base.js`).

**For `has_active_job`, the same pipeline applies exactly:**
- Add metric name `'has_active_job'` to `SNAPSHOT_METRICS` (Step D) → ChartDataView treats it as a snapshot metric.
- ChartDataView aggregation: `MAX(Cast('has_active_job', IntegerField()))` (PostgreSQL requires int cast before MAX) → bucket = 1 if any active job, else 0. Bar chart (same as error_frequency).
- Template: add `{% include "partials/_chart_card.html" with canvas_id="chartActiveJob" title="Job Status" %}` in `rig_detail.html` charts tab (line 116 area, near `chartErrorFreq`).
- Chart loader registry: `function () { return Loaders.loadChart('chartActiveJob', 'has_active_job', uuid, range, 'Active %', 'rgba(255,215,0,0.8)', 'rgba(255,215,0,0.15)'); },` (line chart, gold, label 'Active Job', no float fraction needed — integer 0/1 from MAX). Place it after system charts (after `chartErrorFreq` line 73), maintaining 22-chart order.
- Unit: none (integer 0/1); dataset label = Active Job as `"Active %"`. No byte conversion (`BYTE_TO_GB` not in path).
- Chart type: `line` (not `bar`) — consistent with other time-series status indicators (`uptime_s`, `error_frequency`). If user wants `bar`, change `loadChart` `chartType` param to `'bar'`.

**Files affected by full chart display (Step G — only after A-F are verified):**
- `gpu_monitor/templates/dashboard/rig_detail.html` — add chart card include.
- `gpu_monitor/static/js/chart-runtime.js` — add loader to `buildLoaders()` array.
- `gpu_monitor/static/js/chart-colors.js` (optional) — define a gold/yellow palette for active/inactive distinction.
- No serializer/model/view change required beyond Step B/C/D (already covered).

---

## 4. Self-Contained Chart-Display Step — Optional After Core Steps

### Step G — Add Historical Chart in Rig Detail (only after A-F verified)
Only activates the UI display; requires no DB/model change.
1. `rig_detail.html`: include `{% include ... canvas_id="chartActiveJob" title="Job Status (Active %)" %}`.
2. `chart-runtime.js`: register loader (`loadChart` with `metric='has_active_job'`).
3. `chart-colors.js` (optional): gold/yellow series.
4. `chart-base.js`: no change needed; y-axis label will show `Active %` via `unit` param.

---

## 5. Future-Proof Analysis — Separate Job-State Timeseries Model

The user explicitly asked: *what if we want more detailed separate timeseries model for job-state history — not only 0/1 state but also docker image that ran, GPU processes, or future job types (mining, video generation, image generation, jupyter notebook, model training, etc.)?*

### 4.1 Why MetricSnapshot's `has_active_job` is correct NOW
- Consistent with existing timeseries design: MetricSnapshot = one row/heartbeat, all dynamic metrics.
- Simple bool → float → AVG works for "fraction active in bucket".
- No extra table = no extra FK, no extra compaction config, no extra query.
- For the user's stated need ("is rig active or not, over time"), this is sufficient.

### 4.2 Why a separate model becomes necessary LATER
If future requirements include:
- **What image ran?** → Need `docker_image` per time point.
- **What GPU processes ran?** → Need `gpu_processes_json` historically (currently only in LatestSnapshot, removed from timeseries in migration 0047).
- **What job type?** → Mining, video generation, image generation, jupyter, training — needs a categorical field (`job_type`).
- **Multiple concurrent jobs?** → A single bool can't represent "2 jobs running"; need a count or a list.
- **Job duration / start-end tracking?** → Need event-based records (`JobEvent`) rather than snapshot-based.

### 4.3 Proposed Future Model Design (plan-ready, not implemented)

**Option A: JobStateMetric (snapshot-based, like MetricSnapshot)**
```
class JobStateMetric(models.Model):
    snapshot = FK(MetricSnapshot, CASCADE)  # links to parent heartbeat
    timestamp = DateTimeField(db_index=True)
    rig_uuid = UUIDField(db_index=True)
    has_active_job = BooleanField(default=False)
    job_type = CharField(max_length=32, blank=True, default='')  # mining/video/jupyter/training/etc.
    docker_image = CharField(max_length=255, blank=True, default='')
    gpu_processes_json = JSONField(default=list, blank=True)  # [{gpu_index, pid, name, type, gpu_mem_mb}]
    active_job_count = PositiveSmallIntegerField(default=0)  # for multi-job
```
- Pros: Historical, links to parent MetricSnapshot, works with ChartDataView aggregation.
- Cons: 1 row per heartbeat × potential multiple job states; needs compaction entry; more complex query.

**Option B: JobEvent (event-based, like RigStatusEvent)**
```
class JobEvent(models.Model):
    rig_uuid = UUIDField(db_index=True)
    timestamp = DateTimeField(default=timezone.now, db_index=True)
    event_type = CharField(max_length=16)  # started / ended / changed
    job_type = CharField(max_length=32)
    docker_image = CharField(max_length=255)
    gpu_processes_json = JSONField(default=list)
```
- Pros: Accurate duration tracking, no redundant rows when no change, efficient for event analytics.
- Cons: Not directly compatible with ChartDataView's bucket AVG pattern (needs different aggregation logic); more complex for "fraction active over time" queries.

**Option C: Hybrid (recommended for future readiness)**
- Keep `MetricSnapshot.has_active_job` (bool, snapshot-based) for quick "is it active?" charts.
- Add `JobStateMetric` (snapshot-based with extended fields) ONLY when detailed historical analysis is needed.
- Add `JobEvent` ONLY when exact start/end/duration tracking is needed.
- All three can coexist: `MetricSnapshot` for fast charts, `JobStateMetric` for detailed historical charts, `JobEvent` for duration reports.

### 4.4 Plan Updates Required for Future Readiness
To make the current plan ready for future job-state expansion:

1. **Migration naming**: `0051_metric_snapshot_has_active_job` — explicit, can coexist with future `0052_job_state_metric`.
2. **Serializer**: The `defaults` dict structure should be modular — if `JobStateMetric` is added, create a separate `update_or_create()` call with its own `defaults`, not mixing with MetricSnapshot.
3. **ChartDataView**: Define metric mapping as a dict (already partially done with `SNAPSHOT_METRICS`, `GPU_METRICS`, etc.). For future job-state chart, add `JOB_STATE_METRICS` mapping to either MetricSnapshot or the new JobStateMetric model.
4. **Compaction**: Any new timeseries table must be added to `COMPACT_TABLES` in `compact_data.py` with appropriate `group_by` and `agg_fields` (e.g., `has_active_job` → `avg`, `job_type` → `last`, `docker_image` → `last`).
5. **Cleanup**: Any new table with `timestamp` must be added to `CLEANUP_TABLES` in `cleanup_old_data.py`.
6. **Agent payload**: The payload already sends `gpu_processes` and `docker_containers`. When future job-type is needed, the agent should send a new field (e.g., `metrics['job_type']` or `metrics['job_state']`) that the serializer maps to either MetricSnapshot or the new JobStateMetric.

---

## 5. Findings & Recommendations

### Finding 1: MetricSnapshot is the right place for `has_active_job` — BUT compaction/cleanup must be updated
- Confirmed: MetricSnapshot IS compacted (`compact_data.py` line 104-119) and IS cleaned (`cleanup_old_data.py` line 34). Adding `has_active_job` requires: (1) `metric_app/management/commands/compact_data.py` — add `'has_active_job': 'avg'` to MetricSnapshot `agg_fields` (line 107-115); (2) serializer `defaults` — include `'has_active_job': validated.get(...)`; (3) `SNAPSHOT_METRICS` — include `'has_active_job'`. Without (1), the field is lost during tier-2 (15m) and tier-3 (1h) compaction (bool 0/1 → AVG = fraction active over bucket). Cleanup (delete by timestamp) needs no column-level change.
- ChartDataView already aggregates MetricSnapshot with `Avg` — bool works with MAX (PostgreSQL native) — no float conversion needed.
- Consistent with existing pattern (`cpu_utilization_pct`, `cpu_temp_c`, etc.).

### Finding 2: The serializer currently misses the write
- `IngestSerializer` reads `has_active_job` but the `defaults` dict (MetricSnapshot) doesn't include it. **This is the only code gap for the immediate feature.**

### Finding 3: A separate JobStateMetric model is NOT needed for the current feature
- The user's stated need (active/not active chart) is satisfied by MetricSnapshot.
- A separate model introduces new FK relationships, new compaction config, new cleanup entries, new chart query paths — unnecessary complexity for a single bool metric.

### Finding 4: The plan must document the future path
- Document the 3-layer defense (code + system check + skill update) for `has_active_job`.
- Document the future `JobStateMetric` design (Option A/C) so if the user later asks for "docker image history" or "job type chart", the architecture is ready.
- Document that the agent payload (`gpu_processes`, `docker_containers`) already contains the data needed for future job-state expansion — no agent changes needed for basic active/inactive tracking.

### Finding 5: System check must cover the new metric
- Per user's W001/W004 defense: add `dashboard.checks` (or `metrics_app.checks`) that verifies:
  - `MetricSnapshot.has_active_job` exists.
  - `SNAPSHOT_METRICS` contains `has_active_job`.
  - Serializer `defaults` writes it.
- This prevents future commits from accidentally removing the field or breaking chart aggregation.

---

## 6. Action Items — Before Implementation (User Approval Required)
Per user's workflow: NEW BRANCH FIRST (done: `plan/chart-job-status`) → plan → approval → implement on branch → push to branch → user merges.

This document IS the plan. Once approved:

1. Create new feature branch from `plan/chart-job-status`: `feat/chart-job-status`.
2. Apply Layer 1 (migration + serializer + ChartDataView).
3. Apply Layer 2 (system check).
4. Apply Layer 3 (skill update if applicable).
5. Push to branch (`feat/chart-job-status`).
6. User verifies and merges on GitHub — **never push to main, never merge branches.**

---

*Plan written against main @ 8565769. All references verified from source files in workspace: `agent/run.py`, `metrics_app/serializers.py`, `metrics_app/models.py`, `metrics_app/views.py`, `compact_data.py`, `cleanup_old_data.py`, `docs/POSSIBLE_FUTURE_WORK_TIMESCALEDB.md`, `docs/future_plans/DOCKER_MANIFEST_EXTENSION_PLAN.md`.*
