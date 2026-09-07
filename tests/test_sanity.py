"""Sanity check rendered HTML for tag balance and template tag balance."""
import os, sys, re
from pathlib import Path
import django
os.environ['DB_NAME']='gpu_monitor'
os.environ['DB_USER']='gpu_monitor'
os.environ['DB_PASSWORD']='local_dev_password'
os.environ['DB_HOST']='127.0.0.1'
os.environ['DB_PORT']='5432'
# Add the tests/ directory to sys.path so we can import _paths
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import GPU_MONITOR_DIR
# gpu_monitor/ needs to be on sys.path so Django imports work.
sys.path.insert(0, str(GPU_MONITOR_DIR))
os.environ['DJANGO_SETTINGS_MODULE']='gpu_monitor.settings'
django.setup()
from django.test import Client
from django.conf import settings
settings.ALLOWED_HOSTS = ['*']
from accounts.models import User
from rigs.models import Rig
user = User.objects.filter(is_staff=True).first()
rig = Rig.objects.filter(owner=user).first() or Rig.objects.first()
c = Client(SERVER_NAME='localhost')
c.force_login(user)
resp = c.get(f'/dashboard/rigs/{rig.uuid}/htmx-metrics/')
body = resp.content.decode('utf-8')

opens = re.findall(r'<(div|span|p|table|tr|td|th|tbody|thead|h[1-6])\b', body)
closes = re.findall(r'</(div|span|p|table|tr|td|th|tbody|thead|h[1-6])>', body)
if_count = body.count('{% if')
endif_count = body.count('{% endif')
for_count = body.count('{% for')
endfor_count = body.count('{% endfor')

print(f"HTML tag balance: open={len(opens)} close={len(closes)} match={len(opens)==len(closes)}")
print(f"Django tag balance: if={if_count} endif={endif_count} for={for_count} endfor={endfor_count}")
print(f"Body size: {len(body)} bytes")
print(f"OK" if (len(opens)==len(closes) and if_count==endif_count and for_count==endfor_count) else "FAIL")
