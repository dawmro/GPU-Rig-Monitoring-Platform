from django import template
from django.template.defaultfilters import truncatechars
from accounts.models import ApiKey
from rigs.models import Rig, RigTag

register = template.Library()


@register.simple_tag
def audit_target_name(log):
    """Look up the human-readable name for an audit log target."""
    # First check metadata
    if log.metadata_json:
        if log.metadata_json.get('name'):
            return log.metadata_json['name']
        if log.metadata_json.get('rig_name'):
            return log.metadata_json['rig_name']
        if log.metadata_json.get('tag'):
            return log.metadata_json['tag']

    # Fallback: look up from database
    if log.target_type == 'ApiKey' and log.target_id:
        try:
            key = ApiKey.objects.get(id=log.target_id)
            return key.name
        except ApiKey.DoesNotExist:
            pass
    elif log.target_type == 'Rig' and log.target_id:
        try:
            rig = Rig.objects.get(uuid=log.target_id)
            return rig.name
        except Rig.DoesNotExist:
            pass
    elif log.target_type == 'RigTag' and log.target_id:
        try:
            tag = RigTag.objects.get(id=log.target_id)
            return tag.name
        except RigTag.DoesNotExist:
            pass
    elif log.target_type == 'User' and log.target_id:
        from accounts.models import User
        try:
            user = User.objects.get(id=log.target_id)
            return user.email
        except User.DoesNotExist:
            pass

    # Last resort: return truncated ID
    if log.target_id:
        return truncatechars(str(log.target_id), 12)
    return '—'
