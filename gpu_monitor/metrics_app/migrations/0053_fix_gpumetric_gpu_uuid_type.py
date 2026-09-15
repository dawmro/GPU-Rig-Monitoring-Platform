"""Fix DB column: clean empty UUID strings via ORM (avoids UUID parser), alter to CharField."""
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('metrics_app', '0052_readd_gpumetric_gpu_uuid'),
    ]
    operations = [
        migrations.RunSQL(
            # Clean empty UUID strings before type change; use VARCHAR cast to avoid UUID parser error on ""
            sql="UPDATE metrics_gpumetric SET gpu_uuid = NULL WHERE LENGTH(gpu_uuid::varchar) = 0",
            reverse_sql="UPDATE metrics_gpumetric SET gpu_uuid = ''::varchar WHERE gpu_uuid IS NULL",
        ),
        migrations.AlterField(
            model_name='gpumetric',
            name='gpu_uuid',
            field=models.CharField(db_index=True, max_length=64, blank=True, default='',
                                   help_text='Stable GPU UUID or GPU-prefixed identifier for identity tracking; full value shown in charts'),
        ),
    ]
