# Verify /opt DB state
Run: DB_PASSWORD=local_dev_password PYTHONPATH=gpu_monitor:$PYTHONPATH ./gpu_monitor/venv/bin/python -c "
import psycopg2
conn = psycopg2.connect(dbname='gpu_monitor', user='gpu_monitor', password='local_dev_password', host='127.0.0.1', port='5432')
cursor = conn.cursor()
cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='metrics_metricsnapshot' ORDER BY ordinal_position")
cols = [r[0] for r in cursor.fetchall()]
print('DB columns count:', len(cols))
print('has_active_job present:', 'has_active_job' in cols)
# Check actual data values in last hour
cursor.execute("SELECT timestamp, has_active_job FROM metrics_metricsnapshot WHERE timestamp >= now() - interval '1 hour' ORDER BY timestamp DESC LIMIT 5")
rows = cursor.fetchall()
print('Latest 5 rows in last 1h:')
for r in rows:
    print(' ', r[0], 'value=', r[1])
cursor.close(); conn.close()
"
