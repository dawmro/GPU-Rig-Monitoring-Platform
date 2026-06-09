from django.db import migrations, models


class Migration(migrations.Migration):
    """ErrorEventOccurrence was already dropped manually (table + rows deleted).
    This migration only adds the replacement fields to MetricSnapshot.
    """

    dependencies = [
        ('metrics_app', '0009_pcie_link_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='metricsnapshot',
            name='error_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='metricsnapshot',
            name='error_json',
            field=models.JSONField(default=list),
        ),
    ]
