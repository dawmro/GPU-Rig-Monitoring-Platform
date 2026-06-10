from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0011_error_cleanup'),
    ]

    operations = [
        migrations.AddField(
            model_name='gpumetric',
            name='gpu_core_clock_mhz',
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='gpumetric',
            name='gpu_mem_clock_mhz',
            field=models.PositiveIntegerField(null=True),
        ),
    ]
