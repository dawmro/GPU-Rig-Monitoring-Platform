import json
import logging
import hashlib
from django.utils import timezone

logger = logging.getLogger(__name__)


class AuditMiddleware:
    """Middleware to capture audit events from request metadata."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if hasattr(request, '_audit_event'):
            self._log_event(request, response)
        return response

    def _log_event(self, request, response):
        from .models import AuditLog
        event = request._audit_event
        user = request.user if hasattr(request, 'user') and request.user.is_authenticated else None
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR')
        AuditLog.objects.create(
            user=user,
            action=event.get('action', ''),
            target_type=event.get('target_type', ''),
            target_id=event.get('target_id', ''),
            ip_address=ip,
            metadata_json=event.get('metadata', {}),
        )


def log_audit_event(request, action, target_type='', target_id='', metadata=None):
    """Helper to attach an audit event to the current request."""
    request._audit_event = {
        'action': action,
        'target_type': target_type,
        'target_id': str(target_id),
        'metadata': metadata or {},
    }


def compute_error_hash(source, message):
    """Compute a deduplication hash for error events."""
    data = f"{source}:{message}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]
