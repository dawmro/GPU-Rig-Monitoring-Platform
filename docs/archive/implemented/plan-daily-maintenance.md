# Plan: Fix `daily_maintenance` — `has_active_job` Bool `max` Aggregation

Branch: `plan/daily-maintenance-has-active-job` (off `main`, never push main, user merges).
Status: **PLANNED — IMPLEMENTING WITH 4-LAYER DEFENSE**

---

## Root Cause (From Log)

PostgreSQL: `function max(boolean) does not exist` (`compact_data.py`, line 115: `'has_active_job': 'max'`). PostgreSQL `MAX()` on `boolean` column is undefined; needs `Cast('field', IntegerField())` or `ExpressionWrapper(..., output_field=IntegerField())`.

---

## Professional Approach (4-Layer Defense: W001/W004/0052 / Skill Reference)

Per `references/job-status-chart-route.md` (line 19-20):
- Bool metric (`has_active_job`): `Max(Cast('has_active_job', IntegerField()))` for max aggregation.
- `ExpressionWrapper(F('field'), FloatField())` for avg (fraction active, 0-1).

Plan steps (each independent, self-contained):
1. **Plan doc** (this file) — approved.
2. **Code fix** (`compact_data.py`) — replace `'has_active_job': 'max'` with SQL-level `CAST` via Django `ExpressionWrapper` / `Cast` import, or modify SQL generation to emit `MAX(has_active_job::int)`.
3. **System check** (`checks.py`) — verify `'max'` aggregation for bool uses Integer cast; fail at `manage.py check` if not.
4. **Test** — `tests/test_compaction_defense_bool_max.py`: simulate bool column; assert SQL contains `CAST(... AS INTEGER)` or `IntegerField` wrapper; assert no `function max(boolean) does not exist` error.
5. **Skill update** — update `references/job-status-chart-route.md` with the exact SQL/cast pattern.

---

## Implementation Decision

Using option with `Cast` + `IntegerField` (matches skill reference exactly). Implementing all 4 layers in sequence.
