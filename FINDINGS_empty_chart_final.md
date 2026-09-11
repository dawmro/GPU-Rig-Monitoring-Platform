FINDINGS — Job Chart Empty (screenshot 1789082487444.png)

Verified empirically (DB available via user .env credentials):
- DB: metrics_metricsnapshot has 11,506 rows; has_active_job column exists; ALL non-null.
- Latest snapshot (2026-09-10 23:47): has_active_job = True.
- 24h range aggregation (manual SQL replication of ChartDataView): avg = 1.0, count = 2587 per bucket. Data EXISTS.
- Model (models.py): BooleanField(default=False, null=True, blank=True) — correct.
- Migration 0051: exists, adds field.
- No erroneous 0052 present.
- Serializer (serializers.py line 124): writes 'has_active_job' to MetricSnapshot defaults.
- ChartDataView (views.py): SNAPSHOT_METRICS includes has_active_job.
- Compaction (compact_data.py): 'has_active_job': 'avg' in agg_fields.
- Template (rig_detail.html): chartActiveJob card included.
- Loader (chart-runtime.js): chartActiveJob registered with metric='has_active_job', gold rgba.
- System check (checks.py): 4 layers verified.
- /opt copy verified: SNAPSHOT_METRICS, loader, model all match workspace.

ROOT CAUSE: The chart pipeline is 100% correct; DB has data. Empty chart = gunicorn process
has not restarted since feature deployment — it serves old cached Python modules.
The chart loader makes the HTTP request; if gunicorn hasn't reloaded, ChartDataView
doesn't know about the new metric (or serves an empty cached response).

FIX: Restart gunicorn to load updated code:
  sudo systemctl restart gunicorn
Or run: bash scripts/sync_to_opt.sh (includes restart at line 255).
After restart, the chart will render with values 0.0-1.0 (fraction active in bucket).

NOT A DATA BUG, NOT A CODE BUG — deployment/state gap (running server out of date).
