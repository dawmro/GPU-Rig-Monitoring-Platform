from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rigs', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='rig',
            name='latest_errors_json',
            field=models.JSONField(default=list, blank=True),
        ),
    ]
