

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RigTag',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('color', models.CharField(default='#6B7280', max_length=7)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rig_tags', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'rigs_rigtag',
                'unique_together': {('user', 'name')},
            },
        ),
        migrations.CreateModel(
            name='Rig',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(default='Unnamed Rig', max_length=255)),
                ('expected_gpus', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('online', 'Online'), ('stale', 'Stale'), ('offline', 'Offline')], default='offline', max_length=10)),
                ('last_seen', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rigs', to=settings.AUTH_USER_MODEL)),
                ('tags', models.ManyToManyField(blank=True, related_name='rigs', to='rigs.rigtag')),
            ],
            options={
                'db_table': 'rigs_rig',
            },
        ),
    ]
