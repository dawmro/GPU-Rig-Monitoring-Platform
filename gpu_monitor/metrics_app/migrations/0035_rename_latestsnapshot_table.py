from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0034_metricsnapshot_cpu_freq'),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE IF EXISTS metrics_latestsnapshot RENAME TO metrics_latest_snapshot;',
            reverse_sql='ALTER TABLE IF EXISTS metrics_latest_snapshot RENAME TO metrics_latestsnapshot;',
        ),
    ]
