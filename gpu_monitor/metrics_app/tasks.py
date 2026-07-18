"""
Celery tasks for metrics_app app.

Migrates maintenance operations from management commands to Celery tasks.
Tasks run on the maintenance queue with appropriate priorities and timeouts.
Async telemetry ingest tasks.
"""

from celery import shared_task
from django.core.management import call_command
from django.db import connection
from rigs.models import Rig
from accounts.models import ApiKey
from metrics_app.serializers import process_ingest
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, queue='maintenance', priority=3, max_retries=0)
def compact_data(self, phase='all', verbose=False, days=31):
    """
    Compact old metric data into larger time buckets.
    
    Runs 3-tier compaction:
    - Tier 2 (1-7 days): 1-min -> 15-min buckets
    - Tier 3 (7-31 days): 15-min -> 1-hour buckets
    
    Uses PostgreSQL advisory lock to prevent concurrent runs.
    
    Args:
        phase: 'all', 'tier2', or 'tier3'
        verbose: Show detailed per-table statistics
        days: Retention period in days
    
    Returns:
        dict: Result with phase, status, details
    """
    # Advisory lock to prevent concurrent compaction runs
    lock_id = 10000 + {'all': 0, 'tier2': 1, 'tier3': 2}.get(phase, 0)
    
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
        locked = cursor.fetchone()[0]
        if not locked:
            raise self.retry(exc=Exception("Compaction already running"), countdown=300)
    
    try:
        logger.info(f"Starting compaction phase={phase}, days={days}")
        call_command('compact_data', phase=phase, verbose=verbose, days=days)
        logger.info(f"Compaction phase={phase} completed")
        return {'phase': phase, 'status': 'completed'}
    except Exception as e:
        logger.error(f"Compaction phase={phase} failed: {e}")
        raise
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])


@shared_task(bind=True, queue='maintenance', priority=2, max_retries=1)
def cleanup_old_data(self, days=31, verbose=False):
    """
    Delete metric data older than retention period.
    
    Processes tables in FK-safe order (children first, parent last).
    Deletes in batches to avoid long table locks.
    
    Args:
        days: Delete data older than this many days
        verbose: Show detailed per-table statistics
    
    Returns:
        dict: Result with days, status, details
    """
    try:
        logger.info(f"Starting cleanup of data older than {days} days")
        call_command('cleanup_old_data', days=days, verbose=verbose)
        logger.info(f"Cleanup completed for {days} days")
        return {'days': days, 'status': 'completed'}
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        raise


@shared_task(bind=True, queue='maintenance', priority=1, max_retries=0)
def vacuum_analyze(self):
    """
    Run VACUUM ANALYZE on metrics tables after maintenance.
    
    Reclaims dead tuples and updates planner statistics.
    Uses regular VACUUM ANALYZE (not VACUUM FULL) — no exclusive lock.
    
    Returns:
        dict: Result with tables processed, status
    """
    tables = [
        'metrics_gpumetric',
        'metrics_storagemetric', 
        'metrics_networkmetric',
        'metrics_gpu_process',
        'metrics_power_reading',
        'metrics_metricsnapshot',
    ]
    
    try:
        logger.info("Starting VACUUM ANALYZE on metrics tables")
        for table in tables:
            with connection.cursor() as cursor:
                cursor.execute(f'VACUUM ANALYZE {table}')
                logger.info(f"VACUUM ANALYZE completed for {table}")
        
        logger.info("VACUUM ANALYZE completed for all tables")
        return {'tables': len(tables), 'status': 'completed'}
    except Exception as e:
        logger.error(f"VACUUM ANALYZE failed: {e}")
        raise
        

@shared_task(
    bind=True,
    queue='ingest',
    priority=9,
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_kwargs={'max_retries': 3}
)
def process_ingest_payload(self, rig_uuid, payload_dict, user_id, api_key_id, enrolled_by_key_changed=False):
    """
    Process telemetry payload asynchronously.
    
    Returns 202 Accepted immediately, processes payload in background.
    Idempotent via natural key (rig_uuid, schema_version, timestamp).
    
    Args:
        rig_uuid: Rig UUID string
        payload_dict: Full payload dict from agent
        user_id: Owner user ID
        api_key_id: API key ID
        enrolled_by_key_changed: Whether API key changed
    
    Returns:
        dict: {'status': 'accepted'|'duplicate'|'error', 'snapshot_id': str|None, 'message': str}
    """
    
    try:
        rig = Rig.objects.get(uuid=rig_uuid)
        api_key = ApiKey.objects.get(id=api_key_id)
        user = api_key.user
        
        # Verify ownership
        if rig.owner_id != user.id:
            return {'status': 'error', 'message': 'Rig not owned by user'}
        
        # Process payload (same logic as sync view)
        result, status = process_ingest(
            rig_uuid=rig_uuid,
            data=payload_dict,           # Fixed: was payload_dict=payload_dict
            owner_id=user.id,            # Fixed: was user_id=user.id
            rig=rig,
            enrolled_by_key_changed=enrolled_by_key_changed
        )
        
        return {
            'status': result.get('status'),
            'snapshot_id': result.get('snapshot_id'),
            'message': result.get('message', '')
        }
        
    except Rig.DoesNotExist:
        return {'status': 'error', 'message': 'Rig not found'}
    except ApiKey.DoesNotExist:
        return {'status': 'error', 'message': 'API key not found'}
    except Exception as e:
        logger.exception(f"Ingest failed for rig {rig_uuid}: {e}")
        return {'status': 'error', 'message': str(e)}
