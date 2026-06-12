# Chart Generation Workflow — Performance Analysis

## Architecture Overview

The chart generation flow is:
1. Browser loads charts on demand (↻ button or tab switch) via `fetch()`
2. `ChartDataView.get()` uses SQL-level aggregation (TruncHour/TruncMinute + Avg/Sum)
3. Returns JSON: `{labels: [...], datasets: [{label, data: [...]}]}`
4. JavaScript (Chart.js) renders the chart

Historical charts are NOT polled automatically — they load on demand.

## Files Involved
- `metrics_app/views.py` — ChartDataView (~120 lines)

## Current Performance

| Chart | Bucket Size | Data Points | Query Time |
|---|---|---|---|
| 24h | 1-minute | ~1,440 | ~30ms |
| 7d | 1-hour | ~168 | ~150ms |
| 30d | 1-hour | ~720 | ~120ms |
| Error freq 30d | 1-hour | ~720 | ~100ms |

## What Changed

### Before (old implementation)
- Fetched ALL rows from DB into Python Model objects
- Iterated row-by-row to fill time buckets in Python
- `[:10000]` and `[:50000]` queryset limits caused data truncation for 7d/30d charts
- Error frequency fetched ~750K rows and counted in Python
- 30d chart took 2-5 seconds of CPU time

### After (current implementation)
- Uses `annotate(TruncHour('timestamp')).values().annotate(Avg/Sum(...))` — SQL does all aggregation
- Returns exactly the right number of data points (no truncation)
- Single-pass through results to map to labels array (no LIMITS)
- Error frequency uses `Sum('error_count')` on MetricSnapshot (no ErrorEventOccurrence table)
- 30d chart takes ~120ms

## Remaining Considerations

### Caching (Optional)
Chart data could be cached for 30-60s to reduce DB load with multiple users.
Current load: charts are loaded on demand, not polled — demand is low.

### Chart.js Redraw (Minor)
The current implementation destroys and recreates charts on refresh.
Could use Chart.js's `data` update API for smoother transitions.

## Live Metrics (Separate System)

Live metrics use a completely different path from charts:
- `dashboard/views.py _fetch_rig_metrics()` — reads from LatestSnapshot (single row per rig)
- GPU, storage, and network data comes from LatestSnapshot JSON arrays (no timeseries queries)
- Docker container metrics and GPU processes still query timeseries tables (small, fast)
- Historical Charts (ChartDataView) are separate — they read from timeseries tables with SQL aggregation
- Performance: < 100ms per rig (was ~1500ms before snapshot optimization)
