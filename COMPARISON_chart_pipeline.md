COMPARISON — Job Status Chart vs Existing Charts (e.g. Error Frequency, CPU Util)

Data Flow (identical for all):
1. Template include (rig_detail.html) → partial _chart_card.html
2. chart-runtime.js buildLoaders() → loader function with metric name
3. chart-loaders.js loadChart(metric, uuid, range, unit, colors) → builds URL
4. chart-base.js Base.buildChartUrl() → /api/v1/rigs/<uuid>/chart-data/?metric=<name>&range=<h>&bucket_minutes=<m>
5. ChartDataView.get() (metrics_app/views.py) → SNAPSHOT_METRICS dispatch
6. SQL: base_qs.annotate(bucket=TruncMinute('timestamp')) + Avg(metric) → JSON
7. chart-loaders.js renders Chart.js instance with dataset
8. Template displays card with title, timeframe label, canvas

Job Status differences (intentional):
- Metric name: 'has_active_job' (vs 'cpu_utilization_pct', 'error_frequency', etc.)
- Aggregation: AVG (bool 0/1 → fraction 0-1) — same as uptime_s (max) but uses avg
- Unit: '' (empty) — shows fraction, not % or count
- Chart type: 'line' (default) — same as error_frequency uses 'bar' explicitly
- Color: gold/yellow rgba(255,215,0) — distinct from existing palettes
- Compaction: 'avg' in agg_fields — same pattern as cpu_utilization_pct
- Template: added after chartErrorFreq (near bottom of charts tab)
- Loader: added at end of buildLoaders() array — maintains 23-chart order

Verified:
- DB: 11506 MetricSnapshot rows; has_active_job column present; non-null = 11506; avg=1.0 (True)
- /opt code: SNAPSHOT_METRICS, loader, model, serializer, compaction all have metric
- /opt DB (direct SQL): column exists; data present
- /opt gunicorn: last restart Jul 10 (before feature); needs restart after sync

Conclusion: pipeline is fully correct. Empty chart = gunicorn hasn't loaded updated code module. Restart required.
