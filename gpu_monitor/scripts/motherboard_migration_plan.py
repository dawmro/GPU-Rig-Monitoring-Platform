#!/usr/bin/env python3
"""
Step 1 Migration Plan: Move motherboard_json from MetricSnapshot to Rig model

This script documents the complete data flow of motherboard_json and every file
that needs to change when moving it from MetricSnapshot (per-heartbeat) to
Rig (per-rig, updated in place like latest_errors_json).

DATA FLOW ANALYSIS:
==================

CURRENT FLOW (motherboard_json in MetricSnapshot):
  Agent → payload.motherboard → IngestSerializer → MetricSnapshot.motherboard_json
  Live Metrics → _fetch_rig_metrics() → MetricSnapshot.objects.filter().first()
                 → template metric_snapshot.motherboard_json.manufacturer/model/bios_version

PROPOSED FLOW (motherboard_json in Rig):
  Agent → payload.motherboard → IngestSerializer → Rig.motherboard_json (update_or_create)
  Live Metrics → _fetch_rig_metrics() → rig.motherboard_json (from Rig object, already loaded)
                 → template metric_snapshot.motherboard_json.manufacturer/model/bios_version
                 (variable name in template stays the same, just changes source)

FILES THAT NEED CHANGES:
=======================

1. rigs/models.py
   - Add motherboard_json field to Rig model
   - Add migration for the new field

2. metrics_app/serializers.py (process_ingest)
   - Instead of writing motherboard_json to MetricSnapshot, write to Rig model
   - Rig.objects.filter(uuid=rig_uuid).update(motherboard_json=motherboard_data)
   - Remove 'motherboard_json' from MetricSnapshot defaults dict

3. metrics_app/models.py (MetricSnapshot)
   - Remove motherboard_json field
   - Add migration for field removal

4. dashboard/views.py (_fetch_rig_metrics)
   - Change: latest_metric_snapshot.motherboard_json → rig.motherboard_json
   - The Rig object is already loaded (rig = get_object_or_404(Rig, uuid=uuid))
   - No extra DB query needed

5. templates/dashboard/_metrics_cards.html
   - Line 343: {% if metric_snapshot.motherboard_json %} → {% if rig.motherboard_json %}
   - Lines 347-352: metric_snapshot.motherboard_json.X → rig.motherboard_json.X
   - Note: The template variable name "metric_snapshot" is misleading after this change.
     Could rename to "motherboard_data" but that's a cosmetic follow-up.

6. metrics_app/management/commands/compact_data.py
   - Remove 'motherboard_json' from static_fields list in COMPACT_TABLES config
   - No other changes needed (field won't exist in MetricSnapshot anymore)

7. metrics_app/management/commands/backfill_historical_data.py
   - Remove 'motherboard_json' from column lists in _insert_snapshot_rows
   - Remove 'motherboard_json' from column lists in _insert_child_rows (if present)
   - Remove from source SELECT and INSERT statements

8. metrics_app/migrations/XXXX_auto.py (new migration)
   - Add motherboard_json to Rig
   - Remove motherboard_json from MetricSnapshot
   - Data migration: copy existing motherboard_json from latest MetricSnapshot per rig to Rig

9. agent/run.py and agent_windows/run.py
   - NO CHANGES — agent still sends motherboard in payload, server handles routing

10. gpu_monitor/metrics_app/admin.py
    - NO CHANGES — admin doesn't reference motherboard_json

BACKWARD COMPATIBILITY:
======================
- Existing data: migration copies motherboard_json from latest MetricSnapshot to Rig
- New heartbeats: serializer writes to Rig instead of MetricSnapshot
- Old heartbeats in DB: motherboard_json stays in MetricSnapshot (historical data preserved)
  but won't be read by Live Metrics (which reads from Rig)
- Charts: NO IMPACT — no chart uses motherboard_json

TESTING CHECKLIST:
=================
1. Run migration: python manage.py migrate
2. Verify Rig.motherboard_json is populated for existing rigs
3. Send test agent payload → verify Rig.motherboard_json is updated
4. Open rig detail page → verify Motherboard card shows data
5. Open rig detail page → verify Motherboard card shows "No data" for rig without mobo data
6. Run compact_data --dry-run → no errors about missing motherboard_json column
7. Run backfill_historical_data --dry-run → no errors about missing column
"""
print(__doc__)
