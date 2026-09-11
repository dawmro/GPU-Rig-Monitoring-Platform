# Fix for empty job chart
Root cause: /opt DB lacks has_active_job column (FieldError in app.log).
DB verified locally: column exists, 11506 rows, avg=1.0.
Server process (/opt gunicorn) hits DB without column — either DB is different or migrate not applied on /opt DB.
Run on /opt server:
  cd /opt/gpu_monitor && source venv/bin/activate && DB_PASSWORD=local_dev_password python manage.py migrate
Then restart gunicorn: sudo systemctl restart gunicorn
