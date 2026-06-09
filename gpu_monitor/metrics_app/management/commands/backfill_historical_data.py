#!/usr/bin/env python3
"""
GPU Rig Monitoring Platform — Historical Data Backfill Script

Populates the database with historical data by repeating recent data.
Useful for testing chart visualization, data retention, and compaction.

How it works:
1. Reads metric data from the last N hours (source window)
2. Repeats it to fill M days, each repetition shifting timestamps back
3. Uses INSERT ... ON CONFLICT DO NOTHING — overlapping rows are skipped
4. Progress reported after each repetition with ETA

Usage:
  cd /opt/gpu_monitor
  source venv/bin/activate && set -a && source .env && set +a

  # Preview
  python manage.py backfill_historical_data --dry-run

  # Default: 9 hours source → 32 days target
  python manage.py backfill_historical_data

  # Custom: 6 hours source → 14 days target
  python manage.py backfill_historical_data --hours 6 --days 14
"""

import logging
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection

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
        self.stdout.write(f'  Source rows: {ns:,} snapshots, {ng:,} gpu, {nd:,} disk, {nn:,} net, {ne:,} errors')

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

        # ── Step 3: Insert data ───────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Step 3: Inserting data…'))
        self.stdout.write('')

        start_time = time.time()
        grand_total = 0

        for rep in range(n_full_reps):
            rep_start = time.time()
            shift_h = source_hours * (rep + 1)
            offset = timedelta(hours=shift_h)

            # Parent snapshots
            id_map = self._insert_snapshots(src['snapshots'], offset)
            snap_count = len(id_map)

            # Child tables
            gpu_count  = self._insert_child_rows(src['gpu'],    'gpu',     id_map, offset)
            disk_count = self._insert_child_rows(src['disk'],   'disk',    id_map, offset)
            net_count  = self._insert_child_rows(src['network'], 'network', id_map, offset)

            # Errors
            err_count = self._insert_errors(src['errors'], offset)

            rep_total = snap_count + gpu_count + disk_count + net_count + err_count
            grand_total += rep_total

            # Progress report
            elapsed = time.time() - start_time
            rep_elapsed = time.time() - rep_start
            rate = rep_total / rep_elapsed if rep_elapsed > 0 else 0
            overall_rate = grand_total / elapsed if elapsed > 0 else 0
            pct = (rep + 1) / n_full_reps * 100
            eta = (elapsed / (rep + 1)) * (n_full_reps - rep - 1)

            self.stdout.write(
                f'  Rep {rep + 1:>3}/{n_full_reps}  '
                f'({pct:5.1f}%)  '
                f'shift {shift_h:>4}h  '
                f'+{rep_total:>7,} rows  '
                f'total {grand_total:>12,}  '
                f'{rate:,.0f} rows/s  '
                f'elapsed {self._fmt_time(elapsed)}  '
                f'ETA {self._fmt_time(eta)}'
            )

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

            id_map = self._insert_snapshots(rem_snap, offset)
            grand_total += len(id_map)
            grand_total += self._insert_child_rows(rem_gpu,  'gpu',     id_map, offset)
            grand_total += self._insert_child_rows(rem_disk, 'disk',    id_map, offset)
            grand_total += self._insert_child_rows(rem_net,  'network', id_map, offset)
            grand_total += self._insert_errors(rem_err, offset)

        # ── Done ──────────────────────────────────────────────────────────
        total_elapsed = time.time() - start_time
        overall_rate = grand_total / total_elapsed if total_elapsed > 0 else 0

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done! {grand_total:,} rows inserted in {self._fmt_time(total_elapsed)} '
            f'({overall_rate:,.0f} rows/s avg)'))

    @staticmethod
    def _fmt_time(seconds):
        """Format seconds as human-readable time."""
        if seconds < 60:
            return f'{seconds:.0f}s'
        elif seconds < 3600:
            m = int(seconds // 60)
            s = int(seconds % 60)
            return f'{m}m {s}s'
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f'{h}h {m}m'

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

    # ── Insert snapshots ─────────────────────────────────────────────────

    def _insert_snapshots(self, snapshots, offset):
        """
        Insert snapshot rows with shifted timestamps.
        Uses ON CONFLICT DO NOTHING — overlapping rows are skipped.
        Returns dict mapping old_id → new_id (only for actually inserted rows).
        """
        id_map = {}
        if not snapshots:
            return id_map

        cols = ('rig_uuid, schema_version, agent_version, timestamp,'
                ' cpu_utilization_pct, cpu_temp_c, cpu_load_avg_json,'
                ' mem_used_bytes, mem_free_bytes, mem_cached_bytes,'
                ' swap_used_bytes, swap_total_bytes, status,'
                ' cpu_model, cpu_physical_cores, cpu_logical_cores,'
                ' mem_total_bytes, software_json, motherboard_json')

        with connection.cursor() as c:
            for i in range(0, len(snapshots), BATCH_SIZE):
                batch = snapshots[i:i + BATCH_SIZE]
                for snap in batch:
                    new_ts = snap['timestamp'] - offset
                    c.execute(f"""
                        INSERT INTO metrics_metricsnapshot ({cols})
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (rig_uuid, schema_version, timestamp) DO NOTHING
                        RETURNING id
                    """, (
                        snap['rig_uuid'], snap['schema_version'], snap['agent_version'],
                        new_ts, snap['cpu_utilization_pct'], snap['cpu_temp_c'],
                        snap['cpu_load_avg_json'], snap['mem_used_bytes'],
                        snap['mem_free_bytes'], snap['mem_cached_bytes'],
                        snap['swap_used_bytes'], snap['swap_total_bytes'],
                        snap['status'], snap['cpu_model'], snap['cpu_physical_cores'],
                        snap['cpu_logical_cores'], snap['mem_total_bytes'],
                        snap['software_json'], snap['motherboard_json'],
                    ))
                    result = c.fetchone()
                    if result:
                        id_map[snap['id']] = result[0]

        return id_map

    # ── Insert child rows ────────────────────────────────────────────────

    def _insert_child_rows(self, rows, table, id_map, offset):
        """Insert child table rows with shifted timestamps and remapped snapshot_id."""
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
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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

    # ── Insert errors ────────────────────────────────────────────────────

    def _insert_errors(self, errors, offset):
        """Insert error occurrences with shifted timestamps."""
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
