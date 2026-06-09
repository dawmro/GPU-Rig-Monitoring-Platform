# Chart Generation Workflow — Performance Analysis

## Architecture Overview

The chart generation flow is:
1. Browser polls `/api/v1/rigs/<uuid>/chart-data/` via HTMX (every 30s)
2. `ChartDataView.get()` builds time buckets, queries DB, fills buckets in Python
3. Returns JSON: `{labels: [...], datasets: [{label, data: [...]}]}`
4. JavaScript (Chart.js) renders the chart

## Files Involved
- `metrics_app/views.py` — ChartDataView (638 lines, 80% of file)
- `gpu_monitor/templates/dashboard/rig_detail.html` — Chart.js rendering
- `gpu_monitor/templates/dashboard/_metrics_cards.html` — Live metrics cards

## Performance Issues Found

### Issue 1: FULL DATASET ITERATION IN PYTHON (Major)
**Severity**: High for 7d/30d charts

**Problem**: Every chart query fetches ALL rows from the database into Python 
Model objects, then iterates them one-by-one to fill buckets.

For a 30-day multi-GPU chart on an 8-GPU rig:
- ~138,000 rows fetched from DB
- Django ORM creates 138,000 Python objects (expensive)
- Python loop iterates all 138,000 rows to fill 720 buckets
- Each row: `getattr()`, timestamp math, list append

**Impact**: 30d chart takes 2-5 seconds of CPU time per request.

**Fix**: Use SQL-level aggregation:
```python
from django.db.models import Avg, Sum, Count
from django.db.models.functions import TruncHour, TruncMinute

# Instead of fetching all rows and aggregating in Python:
GPUMetric.objects.filter(
    rig_uuid=uuid, timestamp__gte=start, timestamp__lte=end
).annotate(
    bucket=TruncHour('timestamp')  # or TruncMinute for 1-min
).values('bucket').annotate(
    avg_value=Avg('gpu_temp_c')
).order_by('bucket')
```
This would reduce data transfer from 138K rows to 720 rows and eliminate 
Python aggregation entirely.

**Trade-off**: More complex SQL queries, harder to maintain. The current 
approach is simpler and works fine for 24h charts (1,440 rows).

---

### Issue 2: ERROR FREQUENCY — NO SQL AGGREGATION (Major)
**Severity**: High for 30d charts

**Problem**: Error frequency fetches ALL occurrences and counts in Python:
```python
for occ in occurrences:  # ~750K rows for 30d
    ts = occ.timestamp.replace(second=0, microsecond=0)
    delta = ts - start_bucket
    idx = int(delta.total_seconds() // bucket_seconds)
    if 0 <= idx < total_buckets:
        error_counts[idx] += 1
```

**Impact**: 30d error chart fetches ~750K rows and iterates them in Python.

**Fix**: Use SQL COUNT with date_trunc:
```python
from django.db.models import Count
from django.db.models.functions import TruncHour

ErrorEventOccurrence.objects.filter(
    rig_uuid=uuid, timestamp__gte=start, timestamp__lte=end
).annotate(
    bucket=TruncHour('timestamp')
).values('bucket').annotate(
    count=Count('id')
).order_by('bucket')
```

---

### Issue 3: DUPLICATE DISCOVERY QUERIES (Minor)
**Severity**: Low

**Problem**: Multi-GPU/Storage/Network/Container/AI charts run TWO queries:
1. `.values('gpu_uuid').distinct()` — discover unique keys
2. Full query — fetch all data

The discovery query scans the time range separately.

**Impact**: 2x DB scans for the same time range. For 30d charts, this adds 
~100ms.

**Fix**: Extract unique keys from the main query results in Python:
```python
gpu_uuids = set(row['gpu_uuid'] for row in data.values('gpu_uuid').distinct())
# OR from already-fetched data:
gpu_uuids = set(row.gpu_uuid for row in gpu_data)
```

**Trade-off**: Minimal impact. The distinct query is on indexed columns.

---

### Issue 4: REPEATED IDENTICAL QUERIES (Minor)
**Severity**: Low

**Problem**: For snapshot metrics, the same queryset is evaluated multiple 
times (once for data, once for uptime, once for load_avg). Django's ORM 
cache helps, but it's not explicit.

**Impact**: Up to 3 identical queries for snapshot metrics.

**Fix**: Explicitly cache with `list()`:
```python
snapshots = list(MetricSnapshot.objects.filter(...))
# Reuse for all _fill_buckets() calls
```

---

### Issue 5: NO CACHING OF CHART DATA (Moderate)
**Severity**: Medium

**Problem**: Every HTMX poll (every 30s) regenerates the entire chart from 
scratch. If 10 users view the same rig's 30d chart, that's 10 identical 
queries.

**Impact**: Unnecessary DB load. Charts don't change between polls (data 
only updates every 60s from agent).

**Fix**: Cache chart data in memory or Redis for 30-60 seconds.

**Trade-off**: Added complexity. May not be needed if DB can handle the load.

---

### Issue 6: CHART.JS REDRAW ON EVERY POLL (Moderate)
**Severity**: Medium

**Problem**: The HTMX swap replaces the entire chart HTML, causing Chart.js 
to destroy and recreate the chart from scratch every 30s.

**Impact**: Visual flicker, CPU usage on client side.

**Fix**: Use Chart.js's `data` update API to update only the data arrays 
instead of recreating the chart.

---

### Issue 7: UNNECESSARY DATA CONVERSION (Minor)
**Severity**: Low

**Problem**: After _fill_buckets, values are converted with list comprehensions:
```python
values = [round(v / (1024**3), 2) if v is not None else None for v in values]
```
This creates a new list for every chart request.

**Impact**: Negligible for 720-1440 buckets.

**Fix**: Convert in the template or JavaScript. Or do it in SQL.

---

## Summary Table

| Issue | Severity | Impact | Effort to Fix |
|---|---|---|---|
| 1. Full dataset iteration in Python | High | 2-5s CPU per 30d chart | Medium (SQL rewrite) |
| 2. Error frequency no SQL agg | High | 750K rows iterated | Low (SQL rewrite) |
| 3. Duplicate discovery queries | Low | ~100ms extra | Low |
| 4. Repeated identical queries | Low | 2-3 extra queries | Low |
| 5. No caching of chart data | Medium | 10x redundant queries | Medium (Redis) |
| 6. Chart.js full redraw | Medium | Client-side flicker | Medium (JS rewrite) |
| 7. Unnecessary data conversion | Low | Negligible | Low |

## Recommended Priority

1. **Issue 2** (Error frequency) — Easy fix, major impact
2. **Issue 1** (SQL aggregation) — Medium effort, major impact  
3. **Issue 5** (Caching) — Medium effort, scales with users
4. **Issue 6** (Chart.js update) — Medium effort, better UX
5. Issues 3, 4, 7 — Low priority, minimal impact
