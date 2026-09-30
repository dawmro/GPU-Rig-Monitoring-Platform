# Migration for disk hardware identifiers on LatestSnapshot
#
# Adds four static JSON arrays (one entry per disk, same order as
# storage_devices_json): model, vendor, serial, wwn — read by the agent
# from /sys/block/<disk>/device/{model,vendor,serial,wwn}.
#
# Static disk identifiers live ONLY in LatestSnapshot (denormalized latest
# state, update_or_create per heartbeat). They are NOT stored in the
# StorageMetric time-series table because no chart or report ever reads
# disk model/vendor/serial/wwn historically — they rarely change (disk
# replacement) and the Live Metrics page only shows the current snapshot.
# This matches the pattern established by migration 0049 (cumulative I/O
# counters -> LatestSnapshot) and 0050 (network static fields ->
# LatestSnapshot).
#
# No compaction change needed: LatestSnapshot is a per-rig latest-state
# row, it is not in compact_data.py COMPACT_TABLES.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0053_alter_gpumetric_gpu_uuid'),
    ]

    operations = [
        migrations.AddField(
            model_name='latestsnapshot',
            name='storage_models_json',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='latestsnapshot',
            name='storage_vendors_json',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='latestsnapshot',
            name='storage_serials_json',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='latestsnapshot',
            name='storage_wwns_json',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
