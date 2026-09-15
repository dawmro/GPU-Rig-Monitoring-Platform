"""Re-add gpu_uuid to GPUMetric (previously removed in 0029 / 0004)."""
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('metrics_app', '0051_metric_snapshot_has_active_job'),
    ]
    operations = [
        migrations.AddField(
            model_name='gpumetric',
            name='gpu_uuid',
            field=models.UUIDField(db_index=True, max_length=64, null=True, blank=True,
                                   help_text='Stable GPU UUID for identity tracking; full value shown in charts'),
        ),
    ]
