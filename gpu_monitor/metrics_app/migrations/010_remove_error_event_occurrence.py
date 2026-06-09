from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0009_pcie_link_fields'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='erroreventoccurrence',
            name='error_event',
        ),
        migrations.DeleteModel(
            name='ErrorEventOccurrence',
        ),
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
