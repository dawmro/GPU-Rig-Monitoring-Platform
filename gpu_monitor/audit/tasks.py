"""
Celery tasks for audit app.

Migrates audit log cleanup from management command to Celery task.
"""

from celery import shared_task
from django.core.management import call_command
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, queue='maintenance', priority=4, max_retries=1)
def cleanup_audit_log(self, days=90, verbose=False):
    """
    Delete audit log entries older than specified days.
    
    Runs after other maintenance tasks to clean up audit trail.
    
    Args:
        days: Delete audit entries older than this many days
        verbose: Show detailed statistics
    
    Returns:
        dict: Result with days, status
    """
    try:
        logger.info(f"Starting audit log cleanup for entries older than {days} days")
        call_command('cleanup_audit_log', days=days, verbose=verbose)
        logger.info(f"Audit log cleanup completed for {days} days")
        return {'days': days, 'status': 'completed'}
    except Exception as e:
        logger.error(f"Audit log cleanup failed: {e}")
        raise
