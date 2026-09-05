"""Add gpu_processes_json and gpu_process_count to LatestSnapshot for
denormalized GPU process display.

GPU process data is now stored only in LatestSnapshot (current snapshot only)
instead of GPUProcessMetric time-series table. This eliminates ~50 INSERT +
1 DELETE per heartbeat per rig, and removes wasted storage from
compaction/cleanup of unused historical data.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0045_clean_docker_duplicates_and_constraint'),
    ]

    operations = [
        migrations.AddField(
            model_name='latestsnapshot',
            name='gpu_processes_json',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='latestsnapshot',
            name='gpu_process_count',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
