# Verified & detailed plan: GPU UUID + model in time-series — Option A (per-row)
Branch: `plan/gpu-uuid-timeseries` (new, off `main` — never push to `main`, never merge before approval)
Status: Planning + verification complete. Option A selected. Full UUID in legend/tooltip (no truncation). Ready for implementation after user approval.

Verified against actual codebase (paths relative to repo root):
- `gpu_monitor/metrics_app/models.py`: `GPUMetric` (lines 61-100), `model` exists (line 73), `gpu_index` (line 72). `gpu_uuid` was previously removed (migrations: `0029_remove_gpumetric_gpu_uuid`, `0004_righardware_remove_gpumetric_gpu_uuid_and_more`). Must be re-added.
- `gpu_monitor/metrics_app/serializers.py`: `GPUMetric.objects.update_or_create` (line 153-177) writes `model`, `gpu_index`, metrics — NO `gpu_uuid` currently (line 159 has `model`, no `gpu_uuid`). Agent payload includes `gpu.get('uuid', '')` (line 179) but it's only stored in `gpu_uuids_json` for `LatestSnapshot` — NOT in `GPUMetric`. Need to add `gpu_uuid` to defaults (line 157-176).
- `gpu_monitor/metrics_app/views.py`: `ChartDataView.get()` (`GPU_METRICS` line 235, `_handle_gpu_metric` line 640-682). Multi-GPU labels built at lines 673-681: `label: f'GPU{key[0]}'`. Must be updated to full UUID + model.
- `gpu_monitor/static/js/chart-loaders.js`: `loadChartMultiGpu` (lines 494-570) expects identity labels with space separator (`"{uuid_part} {model}"`); legend truncation at 12 chars (line 543-544) must be REMOVED for full UUID.
- `gpu_monitor/static/js/chart-runtime.js`: loader registry (lines 47-76) unchanged.
- `agent_windows/run.py`: payload includes GPU array with `uuid` (verify `gpu.get('uuid')` in payload; serializer already reads it).

---

## Decision (final, after verification)
Use **Option A ONLY**: add `gpu_uuid` back to `GPUMetric` (same row as metrics). `model` stays (line 73). `gpu_index` stays (line 72). No separate table (`Option B` rejected). Full UUID in chart legend and tooltip — NO truncation.

---

## What was removed / no longer relevant (cleaned fragments)
- All references to `Option B` (`GPUMetadata` separate table) — removed as active option; only kept brief explanation of why rejected.
- References to 12-char truncation (`D1`-`D4` options) — replaced with single directive: FULL UUID, remove loader truncation override.
- References to `chart-runtime.js` as file to modify — loader logic is in `chart-loaders.js`, not `chart-runtime.js`. `chart-runtime.js` only registers loaders; unchanged.
- Any mention of `MetricSnapshot.gpu_uuid` — `GPUMetric` is the metric table; snapshot holds summary arrays (`gpu_uuids_json` in `LatestSnapshot`). Not relevant to this plan.

---

## Detailed implementation steps (verified against code, ready to apply)

### Step 1 — Migration — re-add `gpu_uuid` to `GPUMetric`
File created: `gpu_monitor/metrics_app/migrations/00XX_readd_gpumetric_gpu_uuid.py` (actual number: find latest in `metrics_app/migrations/`, e.g., `0031_...`).
Content: `AddField` or `AlterField` — `gpu_uuid = models.UUIDField(db_index=True, null=True, blank=True, max_length=64, help_text="Stable GPU hardware UUID")` on `GPUMetric`. Note: previous field was `CharField(max_length=64)`; UUID format fits.

Defense rules (W001, W004, 0052):
- Filter `0052_alter_*` / `0052_remove_*` in `sync_to_opt.sh` before copy-back.
- `blank=True` set.
- Verify `models.py` matches migration (field exists, same type).
- Add `metrics_app/checks.py` check verifying `GPUMetric.gpu_uuid` exists.

### Step 2 — Model update (`metrics_app/models.py` lines 61-100)
Add after line 73 (`model`):
```
gpu_uuid = models.UUIDField(db_index=True, null=True, blank=True, max_length=64,
                           help_text="Stable GPU UUID for identity tracking; full value shown in charts")
```
Keep `gpu_index` (line 72) unchanged. Keep `unique_together` (`rig_uuid`, `timestamp`, `gpu_index`) unchanged (line 93). Optional index: `('rig_uuid', 'gpu_index', 'gpu_uuid', '-timestamp')` for grouping queries.

### Step 3 — Serializer update (`metrics_app/serializers.py` line 157-177)
In `GPUMetric.objects.update_or_create` defaults dict (line 157-176), add:
```
'gpu_uuid': gpu.get('uuid', ''),
```
This uses the payload's `uuid` (line 179 already reads `gpu.get('uuid', '')` for `LatestSnapshot.gpu_uuids_json`). The UUID is now saved BOTH to `GPUMetric.gpu_uuid` (per-row identity) and `LatestSnapshot.gpu_uuids_json` (summary array — unchanged).

No other serializer changes needed. Payload format (`agent_windows/run.py`) already sends `uuid` per GPU; verify payload dict has `uuid` key — if not present, agent must include it.

### Step 0 — Pre-flight verification (before any edit)
Run: `python manage.py check --fail-level=ERROR`. Confirm `models.py`, `serializers.py`, `views.py` load without errors. Confirm agent `run.py` has `uuid` in GPU payload (`gpu.get('uuid')`). Confirm branch `plan/gpu-uuid-timeseries` is active (`git branch --show-current`), `main` untouched (`git status` clean except docs file).

---

### Step 4 — Chart endpoint (`metrics_app/views.py` lines 640-682) — full UUID label (detailed sub-steps)
In `_handle_gpu_metric`:
- Multi-GPU case (lines 664-682): change label construction from:
  `label: f'GPU{key[0]}',` (line 679)
  to:
  `label: f"GPU-{gpu_uuid} {model}"` — but endpoint must fetch UUID/model per group.

How: since `group_by_keys` is currently `['gpu_index']`, we need UUID and model per group. Two approaches:

**Approach A (simplest, no DB schema change needed beyond step 2)**:
Modify query to include `gpu_uuid` and `model` in aggregation or post-process. Since `group_by_keys` is `['gpu_index']`, multiple UUIDs at same index over time would be averaged/merged incorrectly. Solution: add `gpu_uuid` and `model` to `group_by_keys`, so groups are (`gpu_index`, `gpu_uuid`, `model`). But that splits same index across different UUIDs (replacement) — correct for identity tracking.

Implementation in `_handle_gpu_metric` (line 664):
- Change `group_by_keys` from `['gpu_index']` to `['gpu_index', 'gpu_uuid', 'model']`.
- Update `_read_prebucketed` call (line 665-669) accordingly.
- Update sorted_keys and dataset label (lines 671-681):
  `label: f"GPU-{key[1]} {key[2]}"` (full UUID at `key[1]`, model at `key[2]`).
  Note: `key` is tuple `(gpu_index, gpu_uuid, model)` after change.

But this changes grouping: each unique (`index`, `uuid`, `model`) is a separate series. For current data (no replacement), same UUID + same index + same model = one series per GPU. Correct.

If user wants series grouped by UUID (not index), change `group_by_keys` to `['gpu_uuid', 'model', 'gpu_index']` — but then card position (slot) isn't preserved in grouping. Recommended: keep index in group (as above) so chart loader knows which slot; label shows full UUID + model.

Wait — the loader (`chart-loaders.js`) expects `multi_gpu` charts to have series per GPU. If groups are by (`index`, `uuid`, `model`), each series is uniquely identified. The loader's legend uses `label.text` (full UUID + model) — perfect for full UUID display.

**Verification that no break occurs**:
- `_read_prebucketed` uses `group_by_keys` dynamically; changing the list changes grouping but query structure (`.values(*group_by_keys, 'bucket')`) remains valid.
- For single GPU (`multi_gpu=False`, lines 646-662): `group_by_keys=['gpu_index']` stays unchanged; label is `f'GPU {gpu_index}'` — but user wants full UUID. Change single-GPU label too: `label: f"GPU-{uuid} {model}"` — but single GPU endpoint needs UUID/model for the selected index. Add fetch of UUID/model for the requested `gpu_index` from `GPUMetric` (or `LatestSnapshot.gpu_uuids_json`).

Simpler single-GPU approach: if single GPU (line 646-662), query `GPUMetric` for UUID/model at that index (latest row) and build label with full UUID + model.

**Detailed endpoint code (line 640-682) after change**:
```
if not multi_gpu:
    # Get UUID/model for selected index from latest metric row
    latest = GPUMetric.objects.filter(
        rig_uuid=uuid, gpu_index=gpu_index,
        timestamp__gte=start_bucket, timestamp__lte=end_bucket
    ).order_by('-timestamp').first()
    label_uuid = getattr(latest, 'gpu_uuid', None) or f"gpu-{gpu_index}"
    label_model = getattr(latest, 'model', '') or 'Unknown'
    groups = self._read_prebucketed(... group_by_keys=['gpu_index'] ...)
    ... build values ...
    return {'labels': labels, 'datasets': [{'label': f"GPU-{label_uuid} {label_model}", 'data': values}]}

# multi_gpu: group by (index, uuid, model)
groups = self._read_prebucketed(
    GPUMetric, uuid, db_field, ...,
    group_by_keys=['gpu_index', 'gpu_uuid', 'model'],
    agg_func=agg_func,
)
sorted_keys = sorted(groups.keys(), key=lambda k: (k[0], k[1], k[2]))
datasets = []
for key in sorted_keys:
    ...
    datasets.append({
        'label': f"GPU-{key[1]} {key[2]}",  # full UUID at index 1, model at 2
        'data': values,
    })
```
This ensures every series label is full UUID + model.

### Step 5 — Chart loader (`chart-loaders.js` lines 532-549) — remove truncation
Remove the `generateLabels` override (or keep but disable truncation). Since user wants full UUID, the simplest is to delete the entire `generateLabels` function inside `loadChartMultiGpu`'s options (lines 530-551), so Chart.js default legend uses `label.text` unchanged.

Also verify tooltip (lines 553-563): `label: function(ctx) { return ctx.dataset.label + ': ' + ... }` — uses full `dataset.label` (full UUID + model) — correct.

No change needed to `chart-runtime.js` (loader registry unchanged).

### Step 6 — Chart loader verification (no regression)
Before: `loadChartMultiGpu` expects `multi_gpu: 'true'`; endpoint returns `datasets` with `label` and `data`. After: endpoint returns same structure, just `label` uses full UUID + model. Loader doesn't parse UUID/model from label (only uses `label.text` for legend/tooltip). No JS break.

### Step 7 — Template / card (`rig_detail.html` / `_metrics_cards.html`)
`_metrics_cards.html` line 376-377 shows `gpu.gpu_uuid` in a small span (`text-[10px]`). This is separate from chart labels; unchanged.
Card titles unchanged (`"GPU Utilization"`, etc.).

### Step 8 — Tests (`tests/test_chart_logic.py` or similar)
Add test: with `multi_gpu=true`, endpoint returns dataset labels containing full UUID (36 chars + model). Verify `label` string length > 40 (includes UUID + model). Verify no `…` in label (truncation removed).

Defend against regression: test that `_handle_gpu_metric` with `multi_gpu=True` returns labels with full UUID when `GPUMetric.gpu_uuid` is set; falls back to `GPU-{index}` only if UUID is null.

### Step 9 — Skill / docs update
Update `gpu-rig-monitoring` skill: document Option A (per-row), full UUID display, loader change (`generateLabels` removed), endpoint grouping (`group_by_keys` includes UUID + model), no truncation.

---

## Verification checklist (before pushing branch)
- [ ] `models.py`: `GPUMetric.gpu_uuid` added; `blank=True`; index optional.
- [ ] Migration applies cleanly (`manage.py migrate`). Filtered from `0052_*` in `sync_to_opt.sh`.
- [ ] `serializers.py`: `gpu_uuid` added to `GPUMetric` defaults; uses `gpu.get('uuid', '')`.
- [ ] `views.py`: `_handle_gpu_metric` groups include UUID/model; labels use full UUID; single-GPU label uses full UUID.
- [ ] `chart-loaders.js`: `generateLabels` truncation override removed or disabled.
- [ ] End-to-end: agent payload with UUID → ingest → DB row has UUID → chart endpoint returns full UUID in label → legend shows full UUID (no `…`).
- [ ] No code breaks (`python manage.py check` passes; `tests/test_chart_logic.py` or new test passes).
- [ ] Branch pushed: `git push ... plan/gpu-uuid-timeseries`; never `main`.

---

---

## Expanded sub-step checklist for Step 4 (endpoint — concrete actions, no skips)
4.1 Open `metrics_app/views.py`; navigate to `_handle_gpu_metric` (line 640).
4.2 Find `group_by_keys = ['gpu_index']` (line 667, multi_gpu path). Replace with `['gpu_index', 'gpu_uuid', 'model']`.
4.3 Find `_read_prebucketed` call (line 665): pass updated `group_by_keys`.
4.4 Find `sorted_keys` (line 671): keep sort key `(k[0], k[1], k[2])` matching new tuple length.
4.5 Find dataset label construction (line 678-680): replace `label: f'GPU{key[0]}'` with `label: f"GPU-{key[1]} {key[2]}"`.
4.6 Find single-GPU path (line 646-662): add `latest = GPUMetric.objects.filter(...)` query; build `label_uuid` and `label_model`; replace `label: f'GPU {gpu_index}'` with `label: f"GPU-{label_uuid} {label_model}"`.
4.7 Save file; run `python manage.py check` — must pass with zero errors.
4.8 Run endpoint test: `curl -X GET .../chart-data/?metric=gpu_util_pct&multi_gpu=true` and verify JSON `datasets[].label` contains full 36-char UUID (e.g., `"GPU-6233c133-3be8... RTX 4090"`).

---

## References (verified paths)
- `gpu_monitor/metrics_app/models.py`: `GPUMetric` (61-100), `model` (73), index (72), `unique_together` (93).
- `gpu_monitor/metrics_app/serializers.py`: `GPUMetric` write (153-177), `gpu_uuids` (134), `latest.gpu_uuids_json` (486).
- `gpu_monitor/metrics_app/views.py`: `ChartDataView.get()` (325+), `GPU_METRICS` (235), `_handle_gpu_metric` (640-682), label format (679-680).
- `gpu_monitor/metrics_app/migrations/`: previous removal (`0029`, `0004`) — new migration must reverse.
- `gpu_monitor/static/js/chart-loaders.js`: `loadChartMultiGpu` (494-570), legend truncation (532-549), tooltip (553-563).
- `gpu_monitor/static/js/chart-runtime.js`: loader registry (46-76), `loadChartMultiGpu` called at line 52.
- `gpu_monitor/dashboard/views.py`: `LatestSnapshot.gpu_uuids_json` usage (line 189, 262).
- Memory / branch rules: `plan/` branch, never `main`, user merges.
