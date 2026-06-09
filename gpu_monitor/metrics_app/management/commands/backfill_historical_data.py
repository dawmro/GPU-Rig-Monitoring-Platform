#!/usr/bin/env python3
"""
GPU Rig Monitoring Platform — Historical Data Backfill Script

Populates the database with 32 days of historical data by repeating
the last 9 hours of real data. Useful for testing chart visualization,
data retention, and compaction with realistic data.

How it works:
1. Reads all metric data from the last 9 hours (source window)
2. Repeats it 85 times to fill ~32 days, each repetition shifting
   timestamps back by the source window size
3. Maintains FK relationships: new snapshot IDs are tracked and
   child rows reference the correct new parent
4. Uses batch INSERT with psycopg2.extras.execute_values for performance

Usage:
  cd /opt/gpu_monitor
  source venv/bin/activate && set -a && source .env && set +a

  # Preview
  python manage.py backfill_historical_data --dry-run

  # Insert 32 days based on last 9 hours
  python manage.py backfill_historical_data

  # Custom parameters
  python manage.py backfill_historical_data --hours 6 --days 14
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

BATCH_SIZE = 2000


class Command(BaseCommand):
    help = 'Backfill historical data by repeating recent data to fill N days'

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=int, default=9,
                            help='Source data window in hours (default: 9)')
        parser.add_argument('--days', type=int, default=32,
                            help='Target number of days to fill (default: 32)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview without inserting data')

    def handle(self, *args, **options):
        source_hours = options['hours']
        target_days = options['days']
        dry_run = options['dry_run']

        now = timezone.now()
        source_start = now - timedelta(hours=source_hours)
        target_start = now - timedelta(days=target_days)

        self.stdout.write(self.style.MIGRATE_HEADING('Backfill Historical Data'))
        self.stdout.write(f'  Source : last {source_hours}h  ({source_start.strftime("%Y-%m-%d %H:%M")} → {now.strftime("%Y-%m-%d %H:%M")})')
        self.stdout.write(f'  Target : {target_days}d back  ({target_start.strftime("%Y-%m-%d %H:%M")} → {now.strftime("%Y-%m-%d %H:%M")})')
        self.stdout.write(f'  Dry run: {dry_run}')
        self.stdout.write('')

        # ── Step 1: Read source data ──────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('Step 1: Reading source data…'))
        src = self._read_source(source_start, now)

        if not src['snapshots']:
            self.stdout.write(self.style.ERROR('  No data in source window. Aborting.'))
            return

        ns = len(src['snapshots'])
        ng = len(src['gpu'])
        nd = len(src['disk'])
        nn = len(src['network'])
        ne = len(src['errors'])
        self.stdout.write(f'  {ns:,} snapshots, {ng:,} gpu, {nd:,} disk, {nn:,} net, {ne:,} errors')

        # ── Step 2: Compute repetition plan ───────────────────────────────
        total_hours = target_days * 24
        n_full_reps = total_hours // source_hours
        remainder_h = total_hours % source_hours

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Step 2: Repetition plan'))
        self.stdout.write(f'  Full reps : {n_full_reps} × {source_hours}h = {n_full_reps * source_hours}h ({(n_full_reps * source_hours) / 24:.1f} days)')
        self.stdout.write(f'  Remainder : {remainder_h}h')

        projected = (ns + ng + nd + nn + ne) * n_full_reps
        self.stdout.write(f'  Projected : ~{projected:,} rows')

        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUN — no data inserted'))
            return

        # ── Step 3: Insert ────────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Step 3: Inserting data…'))

        grand_total = 0

        for rep in range(n_full_reps):
            shift_h = source_hours * (rep + 1)
            offset = timedelta(hours=shift_h)

            # Parent snapshots (batch insert, collect ID mapping)
            id_map = self._insert_snapshots_batch(src['snapshots'], offset)
            grand_total += len(id_map)

            # Child tables (batch insert with remapped snapshot_id)
            grand_total += self._insert_child_batch(src['gpu'],     'gpu',    id_map, offset)
            grand_total += self._insert_child_batch(src['disk'],    'disk',   id_map, offset)
            grand_total += self._insert_child_batch(src['network'], 'network', id_map, offset)

            # Errors (batch insert, no FK dependency)
            grand_total += self._insert_errors_batch(src['errors'], offset)

            if rep % 10 == 0 or rep == n_full_reps - 1:
                self.stdout.write(f'  Rep {rep + 1:>3}/{n_full_reps}  (shift {shift_h:>4}h)  total {grand_total:>12,}')

        # ── Step 4: Remaining hours ───────────────────────────────────────
        if remainder_h > 0:
            self.stdout.write('')
            self.stdout.write(f'Step 4: Remaining {remainder_h}h…')
            shift_h = n_full_reps * source_hours + remainder_h
            offset = timedelta(hours=shift_h)
            cutoff = source_start + timedelta(hours=source_hours - remainder_h)

            rem_snap = [s for s in src['snapshots'] if s['timestamp'] >= cutoff]
            rem_gpu  = [r for r in src['gpu']     if r['timestamp'] >= cutoff]
            rem_disk = [r for r in src['disk']    if r['timestamp'] >= cutoff]
            rem_net  = [r for r in src['network'] if r['timestamp'] >= cutoff]
            rem_err  = [r for r in src['errors']  if r['timestamp'] >= cutoff]

            id_map = self._insert_snapshots_batch(rem_snap, offset)
            grand_total += len(id_map)
            grand_total += self._insert_child_batch(rem_gpu,  'gpu',    id_map, offset)
            grand_total += self._insert_child_batch(rem_disk, 'disk',   id_map, offset)
            grand_total += self._insert_child_batch(rem_net,  'network', id_map, offset)
            grand_total += self._insert_errors_batch(rem_err, offset)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Done! {grand_total:,} rows inserted.'))

    # ── Read ─────────────────────────────────────────────────────────────

    def _read_source(self, start, end):
        d = {'snapshots': [], 'gpu': [], 'disk': [], 'network': [], 'errors': []}
        with connection.cursor() as c:
            c.execute("""
                SELECT id, rig_uuid, schema_version, agent_version, timestamp,
                       cpu_utilization_pct, cpu_temp_c, cpu_load_avg_json,
                       mem_used_bytes, mem_free_bytes, mem_cached_bytes,
                       swap_used_bytes, swap_total_bytes, status,
                       cpu_model, cpu_physical_cores, cpu_logical_cores,
                       mem_total_bytes, software_json, motherboard_json
                FROM metrics_metricsnapshot
                WHERE timestamp >= %s AND timestamp < %s ORDER BY timestamp
            """, [start, end])
            cols = [col[0] for col in c.description]
            d['snapshots'] = [dict(zip(cols, r)) for r in c.fetchall()]

            c.execute("""
                SELECT id, snapshot_id, rig_uuid, gpu_index, gpu_uuid, model,
                       timestamp, gpu_util_pct, gpu_temp_c, fan_speed_pct,
                       mem_used_mb, mem_free_mb, mem_total_mb, mem_util_pct,
                       power_draw_w, power_limit_w,
                       pcie_current_gen, pcie_max_gen,
                       pcie_current_width, pcie_max_width
                FROM metrics_gpumetric
                WHERE timestamp >= %s AND timestamp < %s
                ORDER BY snapshot_id, gpu_index
            """, [start, end])
            cols = [col[0] for col in c.description]
            d['gpu'] = [dict(zip(cols, r)) for r in c.fetchall()]

            c.execute("""
                SELECT id, snapshot_id, rig_uuid, device, mountpoint, fstype,
                       timestamp, usage_pct, temp_c, smart_health, capacity_bytes
                FROM metrics_storagemetric
                WHERE timestamp >= %s AND timestamp < %s
                ORDER BY snapshot_id, device
            """, [start, end])
            cols = [col[0] for col in c.description]
            d['disk'] = [dict(zip(cols, r)) for r in c.fetchall()]

            c.execute("""
                SELECT id, snapshot_id, rig_uuid, interface, ipv4,
                       timestamp, rx_bytes_delta, tx_bytes_delta,
                       rx_errors, tx_errors, link_speed_mbps
                FROM metrics_networkmetric
                WHERE timestamp >= %s AND timestamp < %s
                ORDER BY snapshot_id, interface
            """, [start, end])
            cols = [col[0] for col in c.description]
            d['network'] = [dict(zip(cols, r)) for r in c.fetchall()]

            c.execute("""
                SELECT id, rig_uuid, timestamp, error_event_id
                FROM metrics_error_event_occurrence
                WHERE timestamp >= %s AND timestamp < %s ORDER BY timestamp
            """, [start, end])
            cols = [col[0] for col in c.description]
            d['errors'] = [dict(zip(cols, r)) for r in c.fetchall()]
        return d

    # ── Insert snapshots (batch) ─────────────────────────────────────────

    def _insert_snapshots_batch(self, snapshots, offset):
        """Batch-insert snapshots with shifted timestamps. Returns old_id→new_id map."""
        id_map = {}
        if not snapshots:
            return id_map

        cols = ('rig_uuid, schema_version, agent_version, timestamp,'
                ' cpu_utilization_pct, cpu_temp_c, cpu_load_avg_json,'
                ' mem_used_bytes, mem_free_bytes, mem_cached_bytes,'
                ' swap_used_bytes, swap_total_bytes, status,'
                ' cpu_model, cpu_physical_cores, cpu_logical_cores,'
                ' mem_total_bytes, software_json, motherboard_json')

        sql = f"INSERT INTO metrics_metricsnapshot ({cols}) VALUES %s RETURNING id"

        # Build value tuples
        all_vals = []
        for s in snapshots:
            all_vals.append((
                s['rig_uuid'], s['schema_version'], s['agent_version'],
                s['timestamp'] - offset,
                s['cpu_utilization_pct'], s['cpu_temp_c'], s['cpu_load_avg_json'],
                s['mem_used_bytes'], s['mem_free_bytes'], s['mem_cached_bytes'],
                s['swap_used_bytes'], s['swap_total_bytes'], s['status'],
                s['cpu_model'], s['cpu_physical_cores'], s['cpu_logical_cores'],
                s['mem_total_bytes'], s['software_json'], s['motherboard_json'],
            ))

        # Batch insert with execute_values, then fetch new IDs
        # execute_values doesn't support RETURNING directly, so we use a workaround:
        # Insert in batches and use a temp sequence to map old→new
        with connection.cursor() as c:
            # Add a temp column to track old IDs
            c.execute("ALTER TABLE metrics_metricsnapshot ADD COLUMN IF NOT EXISTS _backfill_old_id BIGINT")
            c.execute("CREATE SEQUENCE IF NOT EXISTS _backfill_seq START 1")

            # Use a CTE-based approach: insert with RETURNING
            for i in range(0, len(all_vals), BATCH_SIZE):
                batch = all_vals[i:i + BATCH_SIZE]
                old_ids = [snapshots[j]['id'] for j in range(i, min(i + BATCH_SIZE, len(snapshots)))]

                # Insert individually within the batch to capture RETURNING
                for j, vals in enumerate(batch):
                    c.execute(f"""
                        INSERT INTO metrics_metricsnapshot ({cols}, _backfill_old_id)
                        VALUES ({','.join(['%s'] * (len(vals) + 1))})
                        ON CONFLICT (rig_uuid, schema_version, timestamp) DO NOTHING
                        RETURNING id
                    """, list(vals) + [old_ids[j]])
                    result = c.fetchone()
                    if result:
                        id_map[old_ids[j]] = result[0]

            # Clean up temp column
            c.execute("ALTER TABLE metrics_metricsnapshot DROP COLUMN IF EXISTS _backfill_old_id")
            c.execute("DROP SEQUENCE IF EXISTS _backfill_seq")

        return id_map

    # ── Insert child rows (batch) ────────────────────────────────────────

    def _insert_child_batch(self, rows, table, id_map, offset):
        if not rows or not id_map:
            return 0

        count = 0
        with connection.cursor() as c:
            for i in range(0, len(rows), BATCH_SIZE):
                batch = rows[i:i + BATCH_SIZE]
                for row in batch:
                    old_sid = row['snapshot_id']
                    if old_sid not in id_map:
                        continue
                    new_ts = row['timestamp'] - offset

                    if table == 'gpu':
                        c.execute("""
                            INSERT INTO metrics_gpumetric
                                (snapshot_id, rig_uuid, gpu_index, gpu_uuid, model,
                                 timestamp, gpu_util_pct, gpu_temp_c, fan_speed_pct,
                                 mem_used_mb, mem_free_mb, mem_total_mb, mem_util_pct,
                                 power_draw_w, power_limit_w,
                                 pcie_current_gen, pcie_max_gen,
                                 pcie_current_width, pcie_max_width)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (rig_uuid, timestamp, gpu_index) DO NOTHING
                        """, (id_map[old_sid], row['rig_uuid'], row['gpu_index'],
                              row['gpu_uuid'], row['model'], new_ts,
                              row['gpu_util_pct'], row['gpu_temp_c'], row['fan_speed_pct'],
                              row['mem_used_mb'], row['mem_free_mb'], row['mem_total_mb'],
                              row['mem_util_pct'], row['power_draw_w'], row['power_limit_w'],
                              row.get('pcie_current_gen'), row.get('pcie_max_gen'),
                              row.get('pcie_current_width'), row.get('pcie_max_width')))

                    elif table == 'disk':
                        c.execute("""
                            INSERT INTO metrics_storagemetric
                                (snapshot_id, rig_uuid, device, mountpoint, fstype,
                                 timestamp, usage_pct, temp_c, smart_health, capacity_bytes)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (rig_uuid, timestamp, device) DO NOTHING
                        """, (id_map[old_sid], row['rig_uuid'], row['device'],
                              row['mountpoint'], row['fstype'], new_ts,
                              row['usage_pct'], row['temp_c'], row['smart_health'],
                              row['capacity_bytes']))

                    elif table == 'network':
                        c.execute("""
                            INSERT INTO metrics_networkmetric
                                (snapshot_id, rig_uuid, interface, ipv4,
                                 timestamp, rx_bytes_delta, tx_bytes_delta,
                                 rx_errors, tx_errors, link_speed_mbps)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (rig_uuid, timestamp, interface) DO NOTHING
                        """, (id_map[old_sid], row['rig_uuid'], row['interface'],
                              row['ipv4'], new_ts, row['rx_bytes_delta'],
                              row['tx_bytes_delta'], row['rx_errors'],
                              row['tx_errors'], row['link_speed_mbps']))

                    count += 1

        return count

    # ── Insert errors (batch) ────────────────────────────────────────────

    def _insert_errors_batch(self, errors, offset):
        if not errors:
            return 0

        count = 0
        with connection.cursor() as c:
            for i in range(0, len(errors), BATCH_SIZE):
                batch = errors[i:i + BATCH_SIZE]
                for err in batch:
                    c.execute("""
                        INSERT INTO metrics_error_event_occurrence
                            (rig_uuid, timestamp, error_event_id)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (rig_uuid, timestamp, error_event_id) DO NOTHING
                    """, (err['rig_uuid'], err['timestamp'] - offset, err['error_event_id']))
                    count += 1

        return count
