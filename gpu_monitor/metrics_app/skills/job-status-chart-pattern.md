---
name: gpu-rig-chart-route-pattern
description: Chart route pattern. Add MetricSnapshot metric: (1) model field + migration, (2) serializer defaults, (3) compact agg_fields (avg/sum/last), (4) SNAPSHOT_METRICS, (5) chart card (rig_detail.html) + loader (chart-runtime.js), (6) system check. Bool 0/1 -> AVG = fraction active.
files:
  - references/job-status-chart-plan.md (corrected compaction/cleanup reality, 6 independent steps A-F + Step G chart display, future JobStateMetric vs JobEvent design)
---

# Chart Route Pattern — Job Status Example (Embedded)

The session produced a concrete, corrected pattern for adding any MetricSnapshot-based historical chart. Key durable lessons (embedded here, not only in memory):

1. MetricSnapshot IS compacted (`compact_data.py` 104-119) and IS cleaned (`cleanup_old_data.py` 34). Any new MetricSnapshot field MUST have an entry in `agg_fields` (`avg`/`sum`/`max`/`min`/`last`) or it is lost in tier-2 (15m) / tier-3 (1h) compaction.
2. For bool (0/1) metrics: use `'avg'` → AVG over bucket = fraction active; chart pipeline (`SNAPSHOT_METRICS` → `Avg` aggregation in `_handle_snapshot_metric`) handles it natively; no byte conversion (`BYTE_TO_GB`/`BYTE_TO_MB`) applies.
3. `sync_to_opt.sh` runs `makemigrations --check`; if `models.py` has `null=True` without `blank=True`, Django generates `0052_alter_...`. Fix: include `blank=True` on model. Also harden sync script to delete any `0052_remove_*` or `0052_alter_*` variants.
4. System check (`metrics_app/checks.py` pattern): verify model field, `SNAPSHOT_METRICS`, serializer defaults, compaction `agg_fields`. Runs on `manage.py check`. Defense-in-depth pattern (W001/W004 class).
5. Chart display pipeline (Step G): `rig_detail.html` includes card → `chart-runtime.js` loader (`loadChart`) with metric name → `chart-loaders.js` builds URL (`Base.buildChartUrl`) → `ChartDataView.get()` (SNAPSHOT_METRICS dispatch + SQL `Avg` + bucket truncation) → JSON response (`{labels, datasets}`) → Chart.js render (`lineDataset`, colors from `chart-colors.js`, options from `chart-base.js`).
6. Empty chart = DB/state gap (no rows, column null/default because migration not applied, or all False), NOT a code regression. Reproduce with Django test client (`force_login`, `SERVER_NAME='localhost'`, `ALLOWED_HOSTS=['*']`) before guessing. Verify: model field exists, serializer writes, compaction aggregates, chart metric mapped.
7. Workflow rules (user preference, embedded): new branch (`plan/` then `feat/`); never push to main; never merge; approval before implement; HTMX > AJAX; empirical > theoretical; Tailwind directly; doc-code sync required; 3-layer defense (code + check + skill/reference).
