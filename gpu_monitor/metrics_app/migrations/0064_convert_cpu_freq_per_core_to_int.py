# Generated migration for converting cpu_freq_per_core_json from objects to ints
# This migration converts existing JSON objects {current_mhz, min_mhz, max_mhz} to simple integers (current_mhz)

from django.db import migrations


def convert_cpu_freq_per_core_to_int(apps, schema_editor):
    """Convert cpu_freq_per_core_json from objects to simple ints (current_mhz only)."""
    MetricSnapshot = apps.get_model('metrics_app', 'MetricSnapshot')
    LatestSnapshot = apps.get_model('metrics_app', 'LatestSnapshot')
    
    # Convert MetricSnapshot records
    for snapshot in MetricSnapshot.objects.all():
        freq_data = snapshot.cpu_freq_per_core_json
        if freq_data and isinstance(freq_data, list):
            converted = []
            for item in freq_data:
                if isinstance(item, dict):
                    # Old format: {"current_mhz": 3400.5, "min_mhz": 800.0, "max_mhz": 3800.0}
                    # Convert to just current_mhz as int
                    current = item.get('current_mhz')
                    if current is not None:
                        converted.append(int(round(current)))
                    else:
                        converted.append(None)
                elif isinstance(item, (int, float)):
                    # Already simple number
                    converted.append(int(round(item)))
                else:
                    converted.append(None)
            if converted != freq_data:
                snapshot.cpu_freq_per_core_json = converted
                snapshot.save(update_fields=['cpu_freq_per_core_json'])
    
    # Convert LatestSnapshot records
    for snapshot in LatestSnapshot.objects.all():
        freq_data = snapshot.cpu_freq_per_core_json
        if freq_data and isinstance(freq_data, list):
            converted = []
            for item in freq_data:
                if isinstance(item, dict):
                    current = item.get('current_mhz')
                    if current is not None:
                        converted.append(int(round(current)))
                    else:
                        converted.append(None)
                elif isinstance(item, (int, float)):
                    converted.append(int(round(item)))
                else:
                    converted.append(None)
            if converted != freq_data:
                snapshot.cpu_freq_per_core_json = converted
                snapshot.save(update_fields=['cpu_freq_per_core_json'])


def reverse_convert_cpu_freq_per_core_to_int(apps, schema_editor):
    """No reverse - we can't restore min/max from current only."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0063_add_cpu_to_gpu_power_ratio'),
    ]

    operations = [
        migrations.RunPython(convert_cpu_freq_per_core_to_int, reverse_convert_cpu_freq_per_core_to_int),
    ]