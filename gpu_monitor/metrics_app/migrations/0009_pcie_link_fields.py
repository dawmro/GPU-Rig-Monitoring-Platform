from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0007_gpu_process_metric'),
    ]

    operations = [
        migrations.AddField(
            model_name='gpumetric',
            name='pcie_current_gen',
            field=models.PositiveSmallIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='gpumetric',
            name='pcie_max_gen',
            field=models.PositiveSmallIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='gpumetric',
            name='pcie_current_width',
            field=models.PositiveSmallIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='gpumetric',
            name='pcie_max_width',
            field=models.PositiveSmallIntegerField(null=True),
        ),
    ]
