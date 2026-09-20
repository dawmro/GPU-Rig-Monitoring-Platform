---
name: compaction-bool-aggregation-pattern
---
# PostgreSQL Bool Aggregation Defense Pattern

## Trigger / When to Apply
When `compact_data.py` or any aggregation script uses `MAX()` or `AVG()` on a `boolean` field (`has_active_job`, any bool in `MetricSnapshot`). PostgreSQL has NO native `MAX(boolean)` or `AVG(boolean)`.

## Fix Pattern (Verified on `plan/daily-maintenance-has-active-job` / commit `f3be63e`)

### For `AVG` (fraction active in bucket — 0.0 to 1.0):
```python
# In SQL generation (compact_data.py line 207):
# For bool fields with agg == 'avg':
ExpressionWrapper(F('has_active_job'), output_field=FloatField())
# Produces SQL: AVG(has_active_job::float)  (implicitly via ExpressionWrapper wrapper)
```

### For `MAX` (any True = 1, else 0):
```python
# In SQL generation (compact_data.py line 209-210):
if agg == 'max' and f == 'has_active_job':
    select_parts.append(f"MAX(CAST({f} AS INTEGER)) AS {f}")
# Produces SQL: MAX(CAST(has_active_job AS INTEGER)) AS has_active_job
```

### For INSERT (back-cast INTEGER result to bool column):
```python
# In insert expression (compact_data.py line 231):
if col == 'has_active_job':
    return f"CAST({col} AS BOOLEAN)"
# Produces SQL: INSERT ... SELECT ..., CAST(has_active_job AS BOOLEAN) AS has_active_job, ...
```

## Import Requirements
```python
from django.db.models import Avg, Sum, F, Max, IntegerField, FloatField, ExpressionWrapper
from django.db.models.functions import Cast
```
Note: `ExpressionWrapper` comes from `django.db.models` (NOT `.functions`).

## Defense Layers (4-layer: W001 / W0052 / skill reference)
1. **Code**: `compact_data.py` SQL generation includes bool-cast condition.
2. **System check** (`checks.py` `E009`): verifies `'CAST(' in src` and `if agg == 'max' and f == 'has_active_job':` present.
3. **Test** (`tests/test_compaction_bool_max_defense.py`): asserts SQL generation; simulates bool column.
4. **Skill reference**: this file; updates `references/job-status-chart-route.md` (line 19-20) with `ExpressionWrapper` + `FloatField()` / `IntegerField()` patterns.

## Verification
- Before: `django.db.utils.ProgrammingError: function max(boolean) does not exist`
- After: `Compaction complete` (verified in production `daily_maintenance` at `plan/daily-maintenance-has-active-job` / commit `66feeaa`).
