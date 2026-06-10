from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0010_metricsnapshot_error_count_metricsnapshot_error_json'),
    ]

    operations = [
        # Remove error_json from MetricSnapshot (only keep error_count integer)
        migrations.RemoveField(
            model_name='metricsnapshot',
            name='error_json',
        ),
        # Delete ErrorEvent model — table already dropped manually
        migrations.DeleteModel(
            name='ErrorEvent',
        ),
    ]
