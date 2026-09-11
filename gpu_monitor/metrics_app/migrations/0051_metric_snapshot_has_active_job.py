# Generated migration — add has_active_job to MetricSnapshot
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('metrics_app', '0050_drop_network_static_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='metricsnapshot',
            name='has_active_job',
            field=models.BooleanField(default=False, null=True, blank=True),
        ),
    ]
