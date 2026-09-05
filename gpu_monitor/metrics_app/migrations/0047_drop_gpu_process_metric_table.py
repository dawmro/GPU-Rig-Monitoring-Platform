"""Drop GPUProcessMetric time-series table.

GPU process data was denormalized to LatestSnapshot.gpu_processes_json in
migration 0046. The time-series table is no longer written to by the
serializer (see comment in process_ingest) and is no longer queried by
any view (live metrics reads from LatestSnapshot directly).

This migration drops the table and its model to:
- Remove dead schema (12 orphaned rows from before migration 0046)
- Eliminate the dead-code DELETE call in dashboard/views.py
- Reduce migration overhead
- Clarify for new developers that this is a one-way decision (the
  time-series was never used for any historical query)

Data preserved: All current GPU process data is in LatestSnapshot.
No historical data loss because no historical query was ever performed.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0046_latestsnapshot_gpu_processes'),
    ]

    operations = [
        migrations.DeleteModel(
            name='GPUProcessMetric',
        ),
    ]
