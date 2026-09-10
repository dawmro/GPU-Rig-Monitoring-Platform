# Job Status Chart — Corrected Implementation (A-F verified)

Corrected session findings (embedded in gpu-rig-monitoring skill):
- MetricSnapshot IS compacted (compact_data.py 104-119) + cleaned (cleanup_old_data.py 34). New field requires agg_fields entry (`'avg'` for bool 0/1) or value lost in tier-2/3.
- Erroneous 0052 migrations (`RemoveField` and `AlterField`) deleted; `sync_to_opt.sh` patched to delete both variants (`*_remove_*` and `*_alter_*`) before copying back.
- Self-contained steps: A (DB), B (serializer), C (compact), D (chart view), E (system check E001-E004), F (skill reference), G (UI chart loader).

Files: 0051 migration, models.py (blank=True), serializers.py defaults, compact_data.py agg_fields, views.py SNAPSHOT_METRICS, checks.py, sync_to_opt.sh defense.
