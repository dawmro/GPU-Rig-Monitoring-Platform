---
name: chart-route-pattern
description: Chart route pattern for MetricSnapshot time-series metrics. Add metric: (1) model + migration, (2) serializer defaults, (3) compact agg_fields, (4) SNAPSHOT_METRICS, (5) chart card + loader, (6) system check. Includes PostgreSQL AVG(boolean) pitfall and sync_to_opt defense.
files:
  - references/job-status-chart-plan.md (corrected: MetricSnapshot IS compacted + cleaned; bool MAX not AVG; sync must clear staticfiles)
---

# Chart Route Pattern — MetricSnapshot Metrics (updated)

Durable lessons from chart-job-status session (embedded, not just in memory):

1. MetricSnapshot IS compacted (`compact_data.py` 104-119) and cleaned (`cleanup_old_data.py` 34). Any new field needs `agg_fields` (`avg`/`sum`/`max`/`min`/`last`). For bool: use `max` (not `avg`) because PostgreSQL `AVG(boolean)` fails (`ProgrammingError`).
2. PostgreSQL `AVG(boolean)` pitfall: `Avg('bool_field')` generates invalid SQL. Fix options: (a) `Avg(ExpressionWrapper(F('bool'), FloatField()))` for float avg (fraction), or (b) `Max(Cast('bool', IntegerField()))` for int 0/1 + bar chart. This session uses option (b) per user's simplification request.
3. `sync_to_opt.sh` defense: include `blank=True` on BooleanField; add `0052_remove_*` / `0052_alter_*` deletion in sync script; clear old staticfiles before `collectstatic --clear` (`find ... -delete`) to prevent nginx serving stale `staticfiles/` (not updated by rsync of workspace `static/`).
4. Empty chart diagnosis = DB/state gap (missing column from unapplied migration, all False values, or missing payload writes), NOT code regression. Reproduce with Django test client (`Client.force_login`, `SERVER_NAME='localhost'`, `ALLOWED_HOSTS=['*']`) before assuming regression.
5. 3-layer defense (user's W001/W004): code fix + system check (`checks.py` E001-E004) + reference file/skill. Never guess without empirical reproduction.
6. User-corrected simplification: don't over-engineer bool→float→AVG with ExpressionWrapper; use `Max` + bar chart (`'bar'`) like `error_frequency`. Keep code minimal.
