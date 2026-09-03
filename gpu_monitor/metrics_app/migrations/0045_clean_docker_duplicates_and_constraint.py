"""Data migration to clean up duplicate LatestDockerContainer rows
and add unique constraint on (rig_uuid, container_id)."""

from django.db import migrations
from django.db.models import Count


def clean_duplicates(apps, schema_editor):
    """Remove duplicate containers, keeping only the first (oldest) per (rig_uuid, container_id)."""
    LatestDockerContainer = apps.get_model('metrics_app', 'LatestDockerContainer')
    
    duplicates = (
        LatestDockerContainer.objects
        .values('rig_uuid', 'container_id')
        .annotate(count=Count('id'))
        .filter(count__gt=1)
    )
    
    for dup in duplicates:
        rows = list(
            LatestDockerContainer.objects
            .filter(rig_uuid=dup['rig_uuid'], container_id=dup['container_id'])
            .order_by('id')
        )
        # Keep first (oldest), delete the rest
        for row in rows[1:]:
            row.delete()


def reverse_clean(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0044_latestsnapshot_has_active_job'),
    ]

    operations = [
        migrations.RunPython(clean_duplicates, reverse_clean),
        migrations.AlterUniqueTogether(
            name='latestdockercontainer',
            unique_together={('rig_uuid', 'container_id')},
        ),
    ]
