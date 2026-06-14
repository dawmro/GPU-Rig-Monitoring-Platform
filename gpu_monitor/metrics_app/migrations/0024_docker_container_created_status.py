from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0023_latestsnapshot_gpu_mem_free_json_and_more'),
    ]

    operations = [
        # Step 1: Drop tables with raw SQL (safe with IF EXISTS)
        migrations.RunSQL(
            sql='DROP TABLE IF EXISTS metrics_dockercontainermetric CASCADE; '
                'DROP TABLE IF EXISTS metrics_latest_docker_container CASCADE',
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Step 2: Delete models from Django state (no DB operations)
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='DockerContainerMetric'),
                migrations.DeleteModel(name='LatestDockerContainer'),
            ],
            database_operations=[],
        ),
        # Step 3: Add docker_containers_json to LatestSnapshot
        migrations.AddField(
            model_name='latestsnapshot',
            name='docker_containers_json',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
