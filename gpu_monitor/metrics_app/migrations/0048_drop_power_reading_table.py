"""Drop PowerReading table — power time-series lives in MetricSnapshot.

PowerReading was originally created as a separate time-series table for
power data, with one row per rig per heartbeat (throttled to 1/minute).
However:
- The chart views (chartCpuPower, chartTotalPower) read from
  MetricSnapshot.cpu_power_w and total_system_power_w
- The chartGpuPower chart reads from GPUMetric.power_draw_w
- No view in the codebase ever queried PowerReading
- LatestSnapshot.power_*_w stores the latest values for Live Metrics

The table grew to 4,374 rows (3 MB) of never-read data, plus required
1 DB read + 1 conditional write per heartbeat just to act as a sentinel
for throttling.

Power time-series data is preserved:
- cpu_power_w and total_system_power_w remain in MetricSnapshot
- power_draw_w remains in GPUMetric (per-GPU)
- LatestSnapshot.power_*_w retains the latest values

This migration drops the metrics_power_reading table to eliminate
the dead schema and the throttling sentinel pattern.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0047_drop_gpu_process_metric_table'),
    ]

    operations = [
        migrations.DeleteModel(
            name='PowerReading',
        ),
    ]
