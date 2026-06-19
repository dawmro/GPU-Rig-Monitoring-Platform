# GPU Rig Monitoring Platform — Database Table Rename Fix

## Problem
The database table was created with the wrong name:
- Actual DB table: `metrics_latestsnapshot` (missing 's' in 'latest')
- Django expects: `metrics_latest_snapshot`

This causes the error:
```
relation "metrics_latestsnapshot" does not exist
LINE 1: SELECT * FROM "metrics_latestsnapshot" LIMIT 1
```

## Solution

### Option 1: Run SQL directly (quickest)
```sql
ALTER TABLE metrics_latestsnapshot RENAME TO metrics_latest_snapshot;
```

### Option 2: Create a Django migration
Run:
```bash
cd /opt/gpu_monitor
source venv/bin/activate
python manage.py makemigrations metrics_app --name rename_latestsnapshot_table
```

Then edit the generated migration to add:
```python
operations = [
    migrations.AlterModelTable(
        name='latestsnapshot',
        table='metrics_latest_snapshot',
    ),
]
```

Then apply:
```bash
python manage.py migrate
```

## Files That Need Updating

The following files reference the wrong table name and should be corrected:

1. `gpu_monitor/metrics_app/models.py` line 292:
   - Change: `db_table = 'metrics_latest_snapshot'` (already correct)

2. All migration files with `model_name='latestsnapshot'`:
   - These use the Django model name (not the DB table name), so they are correct

3. Documentation files:
   - Already fixed in docs/update-all-docs branch
