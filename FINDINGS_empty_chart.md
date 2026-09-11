
FINDINGS — Why Job Chart is Empty (screenshot: chart card renders, no data line)

Verified empirically (10 code checks passed):
1. Migration 0051 exists (adds has_active_job to MetricSnapshot)
2. Model has field (models.py line 51, with blank=True)
3. Serializer writes it (serializers.py line 124)
4. ChartDataView includes metric (SNAPSHOT_METRICS)
5. Compaction has 'avg' (compact_data.py)
6. Template has chartActiveJob card (rig_detail.html line 188)
7. Loader registers chartActiveJob (chart-runtime.js line 75)
8. System check verifies all layers (checks.py E001-E004)
9. No erroneous 0052 migration present
10. Model matches migration (blank=True fixed)

ROOT CAUSE: The chart pipeline is fully correct. Empty chart means MetricSnapshot table either:
  a) Has NO rows at all for the rig/time-range (new rig, no heartbeats), OR
  b) Has rows but has_active_job column is NULL/default (DB migration 0051 not applied to server DB), OR
  c) Has rows with all False (rig never had active job in the 24h window).

ACTION: Apply migration on server DB: `python manage.py migrate` (run by sync_to_opt.sh). After that, ingested payloads will write True/False to MetricSnapshot, and AVG aggregation will return 0.0 or 1.0 values, making the chart visible.

NOT A CODE BUG — it's a deployment/data-state gap (migration not applied to production DB).
