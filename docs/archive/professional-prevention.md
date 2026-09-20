# Professional Prevention — For Each of the 6 Chart-Breaking Problems (from PR #186)

Branch state: fix/revert-chart-uuid-model (d32fb1a) pushed; charts working.
This document records HOW each problem should have been prevented professionally (defense-in-depth: code + system check + test + skill update) per the user's 4-layer defense rule (memory/W001/W004/0052).

---

## Problem 1 — `end_bucket = end_bucket + timedelta(minutes=bucket_minutes)`

What happened: PR #186 extended the chart query window one bucket past now. Fresh agent data (with seconds, not aligned to minute boundary) was excluded; `_read_prebucketed` returned empty `groups`; loader produced empty `datasets`.

How it should have been done professionally:
1. **System check** (`metrics_app/checks.py` per `references/job-status-chart-route.md`): register a check that compares `ChartDataView._build_buckets` output against expected bucket count. If `end_bucket > now + 1 bucket` (i.e., future bucket with no compaction data), the check fails at `manage.py check`. PR #185 design: no extension needed because raw tier-1 (1-min) data covers partial current minute; only add extension if you verify tier-2/3 pre-bucketed data also includes the partial bucket.
2. **Test**: add `tests/test_chart_bucket.py` with a test that asserts `len(datasets[0]['data']) > 0` after a simulated fresh ingest at `17:13:12Z`. If the bucket extension excludes data, the test fails before merge.
3. **Code**: never change bucket boundary logic in the same commit as a new DB field. Separate commits: one for `gpu_uuid` identity tracking; another (if needed) for bucket timing — each independently reviewable and revertable.
4. **Skill**: update `gpu-rig-monitoring` reference `chart-bucket-sizes.md` to document that `end_bucket` must align with `compact_data.py` tier boundaries and must not exceed `now`; any deviation requires a matching `compact_data` change.

---

## Problem 2 — `loadChartMultiGpu` validation guard `!data.datasets || data.datasets.length === 0`

What happened: PR #186 added a validation that threw on empty datasets. The loader's `.catch()` then replaced every chart with `Base.noDataMessage()` — masking the real root cause (empty groups from bucket extension).

How it should have been done professionally:
1. **Defensive guard must be conditional, not fatal**: the professional pattern (per `references/lazy-chart-loading.md`) is to check for empty data and return gracefully (e.g., render a label "No data for this period") WITHOUT destroying the chart instance. The PR #185 loader simply skipped rendering when `data.datasets[0]` missing (`loadChart` line 68 uses `.datasets[0].data` check but still falls through to `.catch()` which shows a message — non-fatal). PR #186's `throw` inside `.then()` broke the chain.
2. **System check**: add `metrics_app/checks.py` that verifies all loader functions export via `window.GRM.ChartLoaders` and that no loader calls `throw` inside `.then()` (only inside `.catch()`). A static JS lint or Python-based JS parse check catches this.
3. **Test**: `tests/test_chart_loader.py` (or a frontend test) should simulate an endpoint returning `{"datasets": []}` and assert the chart canvas is NOT overwritten by `Base.noDataMessage()` — it should either stay empty or show a custom message.
4. **Skill**: `references/chart-rendering-pitfalls.md` should document: "Validation must never throw inside `.then()`; always use conditional return or `.catch()` for empty-state rendering."

---

## Problem 3 — Removing `generateLabels` legend truncation

What happened: PR #186 removed the 23-line `generateLabels` truncation block (line 288–310 removal in `chart-loaders.js` diff). This truncated UUID parts (>12 chars) and interface parts (>8 chars) in the legend. Without it, full UUID strings (`GPU-a322cff7-...-b676c04a38aa`) overflowed the legend box; combined with new dataset labels, this made charts unreadable.

How it should have been done professionally:
1. **UI/legend change must be tested visually**: the user's `ui-ux-redesign` workflow (per `references/ui-ux-redesign.md`) requires that any legend/label change has a visual verification step — a screenshot comparison or at minimum a browser test (`tests/test_legend_width.py`) that asserts legend text width < container width.
2. **Defend against regression**: add a JS-level comment + check: `/* generateLabels MUST truncate UUID to 12 chars — see chart-revert-regression-trace.md */`. If the function is removed, a code review checklist (from `references/stale-code-audit-workflow.md`) flags: "Legend truncation removed — verify legend overflow."
3. **Separate commit**: legend formatting should never be changed in the same PR as data/model changes. If `generateLabels` needs to change (e.g., to handle full UUID differently), it should be its own `feat/` branch with a design approval, not bundled with `plan/gpu-uuid-timeseries`.
4. **Skill update**: `references/gpu-uuid-display-fix.md` (referenced in skill) should document the legend truncation contract: full UUID in tooltip; truncated in legend; removal breaks display.

---

## Problem 4 — Per-series `GPUMetric.objects.filter(...).first()` UUID/model fetch in `_handle_gpu_metric`

What happened: PR #186 added a new DB query per GPU index (`latest_gpu = ...filter(rig_uuid=..., gpu_index=..., timestamp__gte=..., timestamp__lte=...).order_by('-timestamp').first()`). If bucket timing excludes data (problem 1), `latest_gpu` = `None`; label becomes `"GPU-gpu-0 Unknown"`. This changes series identity between refreshes, breaks legend mapping, and causes extra N+1 queries.

How it should have been done professionally:
1. **No new queries inside chart endpoint**: chart endpoints (`ChartDataView.get`) are already optimized (single `GROUP BY` query per metric family, per skill `gpu-rig-monitoring` chart route). Adding extra `.filter().first()` violates the performance contract. Professional approach: include UUID/model in the existing `_read_prebucketed` aggregation (add them to `group_by_keys` or fetch from latest aggregated row in Python after the query), NOT with a separate query.
2. **Defense layer (system check)**: `metrics_app/checks.py` should count queries per `ChartDataView.get()` call. If it exceeds the documented budget (e.g., 4 queries for 24h range per `dashboard/views.py` comments), the check fails.
3. **Test**: `tests/test_chart_view_perf.py` asserts query count using Django's `connection.queries`; PR #186's change would increase query count from 4 to 5+ per request; test catches this.
4. **Skill**: `references/query-performance-patterns.md` should document: "Chart endpoints must not add per-series DB queries; identity fields (UUID/model) must come from the same GROUP BY result, not a separate `.first()` query."

---

## Problem 5 — `compact_data.py`: `gpu_uuid` added to `static_fields` + new `metrics_rig_status_event` block

What happened: PR #186 changed `GPUMetric` compaction `static_fields` from `['model', 'snapshot_id']` to include `'gpu_uuid'`, and added a new `metrics_rig_status_event` compaction block. This altered pre-bucketed aggregation grouping. If older rows have empty `gpu_uuid` (`''`), grouping treats `''` as a distinct value, splitting series and producing empty aggregated buckets.

How it should have been done professionally:
1. **Migration + model + compaction must be synchronized**: the skill `gpu-rig-monitoring` (references `references/job-status-chart-route.md`) requires `defense W001/W004/0052`: any new DB field added to compaction must have `blank=True`, `default=''` in the model (`models.py` line 73), and a matching entry in `compact_data.py` `agg_fields`/`static_fields`. PR #186 did add the model field but didn't verify that empty-string values group correctly.
2. **System check**: `metrics_app/checks.py` should verify that every field in `static_fields` exists in the model and that no new field breaks grouping for rows where value is `''`. The `0052` defense (`sync_to_opt.sh` filter) catches bad migrations, not bad compaction config.
3. **Test**: add `tests/test_compaction_grouping.py` that runs `compact_data` on a dataset with mixed `gpu_uuid` values (`''` and full UUID) and asserts that aggregated bucket counts don't increase unexpectedly (i.e., grouping by `gpu_uuid` with `''` produces same count as grouping without it when all non-empty UUIDs match).
4. **Separate feature branch**: identity tracking (model/migration) should be separate from compaction changes. `plan/gpu-uuid-timeseries` mixed both — professional approach: `feat/gpu-uuid-model` (DB/model/serializer) and `feat/gpu-uuid-compaction` (compact_data) as separate branches, each independently tested.

---

## Problem 6 — Syntax error from manual loader patch (`generateLabels` inserted in wrong location)

What happened: when manually fixing `chart-loaders.js`, the `generateLabels` restoration was inserted at the network loader's `legendOptions` block (line 286) instead of the `loadChartMultiGpu` area. This removed the closing `}),` for `legendOptions`, creating `SyntaxError: missing } after property list` at line 309, which prevented the loader object from being constructed → `TypeError: loadChartMultiGpu is undefined`.

How it should have been done professionally:
1. **Full file restore, not surgical patch**: when a file is broken, the professional approach is `git checkout <working_commit> -- file` (as was done to fix it: `git checkout dba1852 -- gpu_monitor/static/js/chart-loaders.js`), not manual line insertion. Manual insertion is error-prone; full restore guarantees the file matches a verified working state.
2. **Syntax validation**: before committing JS changes, a pre-commit hook or CI step (`node --check` or `python3 -m py_compile` equivalent for JS via `node --syntax-only`) validates syntax. The syntax error would have been caught before commit.
3. **Regression test**: a test that calls `loadChartMultiGpu` (via `window.GRM.ChartLoaders.loadChartMultiGpu`) verifies the loader exists; `TypeError: undefined` would fail the test.
4. **Code review checklist** (per user's memory rules): any manual edit to `chart-loaders.js` must include verification that `loadChartMultiGpu` is present in `window.GRM.ChartLoaders` and that braces balance (`{` == `}`). The user's defense-in-depth (4-layer: code + system check + test + skill) applies to manual patches as well — the manual patch skipped all 4 layers.

---

## Cross-Cutting Professional Principles (from user's memory / skill)

- **Never mix feature types in one PR**: identity (DB/model), chart display (loader/view), compaction (management), and bucket timing are 4 independent concerns. PR #186 combined them → single point of failure.
- **Branch naming**: `fix/revert-chart-uuid-model` was created correctly; PR #186 should have been split into `feat/` branches.
- **Defense W001/W004/0052**: new DB fields need model check, serializer check, migration filter (`sync_to_opt.sh` 0052 defense), compaction check, and loader check.
- **Doc-code sync**: `docs/plan-gpu-uuid-timeseries.md` (plan doc) existed; but the code didn't match the verified pipeline in the doc. Professional: after implementing, verify the pipeline line-by-line against the doc.
- **Empirical > theoretical**: the bugs were discovered by running the code (syntax error, empty charts), not by reading. Professional workflow requires running `manage.py check`, `node --check`, and a test ingest before pushing.
