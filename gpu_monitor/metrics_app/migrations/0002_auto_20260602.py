from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0001_initial'),
    ]

    operations = [
        # Create GPUMetric table
        migrations.CreateModel(
            name='GPUMetric',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('rig_uuid', models.UUIDField(db_index=True)),
                ('timestamp', models.DateTimeField(db_index=True)),
                ('gpu_index', models.PositiveSmallIntegerField(default=0)),
                ('gpu_uuid', models.CharField(blank=True, default='', max_length=64)),
                ('model', models.CharField(blank=True, default='', max_length=255)),
                ('gpu_util_pct', models.FloatField(null=True)),
                ('gpu_temp_c', models.FloatField(null=True)),
                ('fan_speed_pct', models.FloatField(null=True)),
                ('mem_total_mb', models.PositiveIntegerField(null=True)),
                ('mem_used_mb', models.PositiveIntegerField(null=True)),
                ('mem_util_pct', models.FloatField(null=True)),
                ('power_draw_w', models.FloatField(null=True)),
                ('power_limit_w', models.FloatField(null=True)),
            ],
            options={
                'db_table': 'metrics_gpumetric',
                'unique_together': {('rig_uuid', 'timestamp', 'gpu_index')},
            },
        ),
        migrations.AddIndex(
            model_name='gpumetric',
            index=models.Index(fields=['rig_uuid', '-timestamp'], name='metrics_gpu_rig_uuid_ts_idx'),
        ),
        migrations.AddField(
            model_name='gpumetric',
            name='snapshot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='gpu_metrics', to='metrics_app.metricsnapshot'),
        ),

        # Create StorageMetric table
        migrations.CreateModel(
            name='StorageMetric',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('rig_uuid', models.UUIDField(db_index=True)),
                ('timestamp', models.DateTimeField(db_index=True)),
                ('device', models.CharField(blank=True, default='', max_length=255)),
                ('mountpoint', models.CharField(blank=True, default='', max_length=512)),
                ('fstype', models.CharField(blank=True, default='', max_length=32)),
                ('capacity_bytes', models.BigIntegerField(null=True)),
                ('usage_pct', models.FloatField(null=True)),
                ('temp_c', models.FloatField(null=True)),
                ('smart_health', models.CharField(blank=True, default='', max_length=16)),
            ],
            options={
                'db_table': 'metrics_storagemetric',
                'unique_together': {('rig_uuid', 'timestamp', 'device')},
            },
        ),
        migrations.AddIndex(
            model_name='storagemetric',
            index=models.Index(fields=['rig_uuid', '-timestamp'], name='metrics_stor_rig_uuid_ts_idx'),
        ),
        migrations.AddField(
            model_name='storagemetric',
            name='snapshot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='storage_metrics', to='metrics_app.metricsnapshot'),
        ),

        # Create NetworkMetric table
        migrations.CreateModel(
            name='NetworkMetric',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('rig_uuid', models.UUIDField(db_index=True)),
                ('timestamp', models.DateTimeField(db_index=True)),
                ('interface', models.CharField(blank=True, default='', max_length=64)),
                ('ipv4', models.CharField(blank=True, default='', max_length=15)),
                ('link_speed_mbps', models.PositiveIntegerField(null=True)),
                ('rx_bytes', models.BigIntegerField(null=True)),
                ('tx_bytes', models.BigIntegerField(null=True)),
                ('rx_errors', models.PositiveIntegerField(null=True)),
                ('tx_errors', models.PositiveIntegerField(null=True)),
            ],
            options={
                'db_table': 'metrics_networkmetric',
                'unique_together': {('rig_uuid', 'timestamp', 'interface')},
            },
        ),
        migrations.AddIndex(
            model_name='networkmetric',
            index=models.Index(fields=['rig_uuid', '-timestamp'], name='metrics_net_rig_uuid_ts_idx'),
        ),
        migrations.AddField(
            model_name='networkmetric',
            name='snapshot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='network_metrics', to='metrics_app.metricsnapshot'),
        ),

        # Create DockerContainerMetric table
        migrations.CreateModel(
            name='DockerContainerMetric',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('rig_uuid', models.UUIDField(db_index=True)),
                ('timestamp', models.DateTimeField(db_index=True)),
                ('name', models.CharField(blank=True, default='', max_length=255)),
                ('image', models.CharField(blank=True, default='', max_length=255)),
                ('status', models.CharField(blank=True, default='', max_length=32)),
                ('restart_count', models.PositiveIntegerField(default=0)),
            ],
            options={
                'db_table': 'metrics_dockercontainermetric',
                'unique_together': {('rig_uuid', 'timestamp', 'name')},
            },
        ),
        migrations.AddIndex(
            model_name='dockercontainermetric',
            index=models.Index(fields=['rig_uuid', '-timestamp'], name='metrics_docker_rig_uuid_ts_idx'),
        ),
        migrations.AddField(
            model_name='dockercontainermetric',
            name='snapshot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='docker_metrics', to='metrics_app.metricsnapshot'),
        ),

        # Remove JSON fields from MetricSnapshot (data now in separate tables)
        migrations.RemoveField(
            model_name='metricsnapshot',
            name='gpu_metrics_json',
        ),
        migrations.RemoveField(
            model_name='metricsnapshot',
            name='storage_json',
        ),
        migrations.RemoveField(
            model_name='metricsnapshot',
            name='network_json',
        ),
        migrations.RemoveField(
            model_name='metricsnapshot',
            name='ai_processes_json',
        ),
        migrations.RemoveField(
            model_name='metricsnapshot',
            name='docker_containers_json',
        ),
        migrations.RemoveField(
            model_name='metricsnapshot',
            name='errors_json',
        ),

        # Remove JSON fields from LatestSnapshot
        migrations.RemoveField(
            model_name='latestsnapshot',
            name='gpu_metrics_json',
        ),
        migrations.RemoveField(
            model_name='latestsnapshot',
            name='storage_json',
        ),
        migrations.RemoveField(
            model_name='latestsnapshot',
            name='network_json',
        ),
        migrations.RemoveField(
            model_name='latestsnapshot',
            name='docker_containers_json',
        ),
        migrations.RemoveField(
            model_name='latestsnapshot',
            name='errors_json',
        ),
        migrations.RemoveField(
            model_name='latestsnapshot',
            name='software_json',
        ),
    ]
