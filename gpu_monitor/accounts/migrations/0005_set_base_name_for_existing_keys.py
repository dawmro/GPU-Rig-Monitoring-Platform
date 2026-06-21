from django.db import migrations


def set_base_name(apps, schema_editor):
    ApiKey = apps.get_model('accounts', 'ApiKey')
    for key in ApiKey.objects.filter(base_name=''):
        key.base_name = key.name
        key.save(update_fields=['base_name'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_api_key_transfer_fields'),
    ]

    operations = [
        migrations.RunPython(set_base_name, migrations.RunPython.noop),
    ]