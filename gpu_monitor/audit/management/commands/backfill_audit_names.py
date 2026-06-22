from django.core.management.base import BaseCommand
from audit.models import AuditLog
from accounts.models import ApiKey
from rigs.models import Rig, RigTag


class Command(BaseCommand):
    help = 'Backfill audit log target names for old entries'

    def handle(self, *args, **options):
        updated = 0
        skipped = 0

        for log in AuditLog.objects.all():
            if log.metadata_json and log.metadata_json.get('name'):
                skipped += 1
                continue

            name = None
            if log.target_type == 'ApiKey' and log.target_id:
                try:
                    key = ApiKey.objects.get(id=log.target_id)
                    name = key.name
                except ApiKey.DoesNotExist:
                    pass
            elif log.target_type == 'Rig' and log.target_id:
                try:
                    rig = Rig.objects.get(uuid=log.target_id)
                    name = rig.name
                except Rig.DoesNotExist:
                    pass
            elif log.target_type == 'RigTag' and log.target_id:
                try:
                    tag = RigTag.objects.get(id=log.target_id)
                    name = tag.name
                except RigTag.DoesNotExist:
                    pass

            if name:
                if not log.metadata_json:
                    log.metadata_json = {}
                log.metadata_json['name'] = name
                log.save(update_fields=['metadata_json'])
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Updated {updated} audit log entries with target names. '
            f'Skipped {skipped} entries that already had names.'))
