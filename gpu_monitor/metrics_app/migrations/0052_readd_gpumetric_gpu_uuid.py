"""Safe approach: AddField (fresh CharField — works if DB cleaned; safe if column missing post-clean); no RemoveField."""
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('metrics_app', '0051_metric_snapshot_has_active_job'),
    ]
    operations = [
        migrations.AddField(
            model_name='gpumetric',
            name='gpu_uuid',
            field=models.CharField(db_index=True, max_length=64, blank=True, default='',
                                   help_text='Stable GPU UUID or GPU-prefixed identifier; full value in charts'),
            preserve_default=False,
        ),
    ]
