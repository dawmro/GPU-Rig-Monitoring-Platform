"""Data migration to clean up duplicate LatestDockerContainer rows
and add unique constraint on (rig_uuid, container_id).

Also fixes the migration state to reflect that the old
unique_together = ('rig_uuid', 'name') constraint was never actually
created in the database (or was removed), so we use SeparateDatabaseAndState
to update the state without trying to remove the non-existent constraint.
"""

from django.db import migrations, models
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
        # Step 1: Clean up duplicate rows in the database
        migrations.RunPython(clean_duplicates, reverse_clean),

        # Step 2: Update Django's migration state to remove the old
        # unique_together = ('rig_uuid', 'name') that was never actually
        # created in the database. Use SeparateDatabaseAndState with
        # state_operations only to avoid trying to remove a non-existent constraint.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterUniqueTogether(
                    name='latestdockercontainer',
                    unique_together=set(),
                ),
            ],
        ),

        # Step 3: Add the new unique constraint with an explicit name
        migrations.AddConstraint(
            model_name='latestdockercontainer',
            constraint=models.UniqueConstraint(
                fields=['rig_uuid', 'container_id'],
                name='unique_rig_container',
            ),
        ),
    ]
