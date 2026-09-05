"""Drop static field columns (ipv4, link_speed_mbps) from NetworkMetric.

Background
----------
NetworkMetric historically stored two static fields alongside per-minute
dynamic metrics:
- ipv4: interface IPv4 address (string)
- link_speed_mbps: NIC link speed in megabits/sec

These were written every heartbeat even though they only change on:
- DHCP lease renewal (ipv4) — typically every 1-24 hours
- NIC reconfiguration / link renegotiation (link_speed_mbps) — rare

No chart or report ever reads them from the time-series table:
- chart view: only reads rx/tx_bytes_delta, rx/tx_errors
- report endpoint: only reads rx/tx_bytes_delta, rx/tx_errors
- Live Metrics view: reads ipv4 and link_speed_mbps from
  LatestSnapshot.network_ipv4s_json and network_speeds_json (the
  canonical source for current state)

The fields were only preserved by compact_data's 'last' aggregation
to pass through tier compaction cycles — same self-referencing pattern
as the StorageMetric cumulative counters (migration 0049).

Fix
---
- Removed ipv4 and link_speed_mbps from NetworkMetric model
- Removed the 'last' aggregation for these fields in compact_data
- Updated Django admin list_display/search_fields to use remaining columns
- Migration drops the 2 columns from the database

Storage savings (current DB)
------------------------------
15,513 rows × 2 columns × (4-8 bytes/column) ≈ 100 KB saved immediately.
For a 2-interface rig at 60s heartbeat:
- Per heartbeat: ~12 bytes saved × 2 interfaces = 24 bytes
- Per day: 24 × 60 × 24 = 34 KB/rig/day
- For 100 rigs: 3.4 MB/day of static field writes eliminated

Where the data still lives
----------------------------
- LatestSnapshot.network_ipv4s_json  (per interface, per rig)
- LatestSnapshot.network_speeds_json (per interface, per rig)

These are the canonical source for Live Metrics display, and the
agent continues to send these values in every payload (no agent change).

Backward compatibility
-----------------------
- No agent code change required (agent still sends ipv4 and link_speed)
- No chart/report query change (they never read these columns)
- Django admin list_display updated to use remaining columns
- Migration is a non-blocking DROP COLUMN (PG 12+)
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0049_drop_storage_cumulative_counters'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='networkmetric',
            name='ipv4',
        ),
        migrations.RemoveField(
            model_name='networkmetric',
            name='link_speed_mbps',
        ),
    ]
