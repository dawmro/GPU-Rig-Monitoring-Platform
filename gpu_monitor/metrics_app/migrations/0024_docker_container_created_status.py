from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0023_latestsnapshot_gpu_mem_free_json_and_more'),
    ]

    operations = [
        # Drop DockerContainerMetric table if it exists (model already deleted from code)
        migrations.RunSQL(
            sql='DROP TABLE IF EXISTS metrics_dockercontainermetric CASCADE',
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Drop LatestDockerContainer table if it exists (data moved to LatestSnapshot)
        migrations.RunSQL(
            sql='DROP TABLE IF EXISTS metrics_latest_docker_container CASCADE',
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Add docker_containers_json to LatestSnapshot
        migrations.AddField(
            model_name='latestsnapshot',
            name='docker_containers_json',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
