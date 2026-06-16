#!/usr/bin/env python3
"""
GPU Rig Monitoring Platform — Historical Data Backfill Script

Populates the database with historical data by repeating recent data.
Uses execute_values for high-performance batch inserts (~50K rows/sec).

Usage:
  python manage.py backfill_historical_data --dry-run
  python manage.py backfill_historical_data --hours 8 --days 10
"""

import logging
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000


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
        ne = sum(s.get('error_count', 0) for s in src['snapshots'])
        err_per_snap = round(ne / ns) if ns > 0 else 0
        self.stdout.write(f'  {ns:,} snapshots, {ng:,} gpu, {nd:,} disk, {nn:,} net, {ne:,} errors (~{err_per_snap}/snap)')

        # ── Step 2: Compute repetition plan ───────────────────────────────
        total_hours = target_days * 24
        n_full_reps = total_hours // source_hours
        remainder_h = total_hours % source_hours

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Step 2: Repetition plan'))
        self.stdout.write(f'  Full reps : {n_full_reps} × {source_hours}h = {n_full_reps * source_hours}h ({(n_full_reps * source_hours) / 24:.1f} days)')
        self.stdout.write(f'  Remainder : {remainder_h}h')
        projected = (ns + ng + nd + nn) * n_full_reps
        self.stdout.write(f'  Projected : ~{projected:,} rows ({ne * n_full_reps:,} total error points, ~{err_per_snap}/snap)')

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

            # Parent snapshots (need ID mapping for child FK)
            id_map = self._insert_snapshots(src['snapshots'], offset, err_per_snap)
            snap_count = len(id_map)

            # Child tables (use remapped snapshot_id)
            gpu_count  = self._insert_child_rows(src['gpu'],    'gpu',     id_map, offset)
            disk_count = self._insert_child_rows(src['disk'],   'disk',   id_map, offset)
            net_count  = self._insert_child_rows(src['network'], 'network', id_map, offset)

            rep_total = snap_count + gpu_count + disk_count + net_count
            grand_total += rep_total

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

            id_map = self._insert_snapshots(rem_snap, offset, err_per_snap)
            grand_total += len(id_map)
            grand_total += self._insert_child_rows(rem_gpu,  'gpu',     id_map, offset)
            grand_total += self._insert_child_rows(rem_disk, 'disk',   id_map, offset)
            grand_total += self._insert_child_rows(rem_net,  'network', id_map, offset)

        total_elapsed = time.time() - start_time
        overall_rate = grand_total / total_elapsed if total_elapsed > 0 else 0

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done! {grand_total:,} rows inserted in {self._fmt_time(total_elapsed)} '
            f'({overall_rate:,.0f} rows/s avg)'))

    @staticmethod
    def _fmt_time(seconds):
        if seconds < 60:
            return f'{seconds:.0f}s'
        elif seconds < 3600:
            return f'{int(seconds // 60)}m {int(seconds % 60)}s'
        else:
            return f'{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m'

    def _read_source(self, start, end):
        d = {'snapshots': [], 'gpu': [], 'disk': [], 'network': []}
        with connection.cursor() as c:
            c.execute("SELECT id, rig_uuid, schema_version, timestamp, "
                      "cpu_utilization_pct, cpu_temp_c, cpu_load_avg_json, "
                      "mem_used_bytes, mem_free_bytes, mem_cached_bytes, "
                      "swap_used_bytes, swap_total_bytes, status, "
                      "uptime_s, error_count "
                      "FROM metrics_metricsnapshot "
                      "WHERE timestamp >= %s AND timestamp < %s ORDER BY timestamp", [start, end])
            cols = [col[0] for col in c.description]
            d['snapshots'] = [dict(zip(cols, r)) for r in c.fetchall()]

            c.execute("SELECT id, snapshot_id, rig_uuid, timestamp, gpu_index, model, "
                      "gpu_util_pct, gpu_temp_c, fan_speed_pct, "
                      "mem_total_mb, mem_used_mb, mem_free_mb, mem_util_pct, "
                      "power_draw_w, power_limit_w, "
                      "pcie_current_gen, pcie_max_gen, pcie_current_width, pcie_max_width, "
                      "gpu_core_clock_mhz, gpu_mem_clock_mhz "
                      "FROM metrics_gpumetric "
                      "WHERE timestamp >= %s AND timestamp < %s "
                      "ORDER BY snapshot_id, gpu_index", [start, end])
            cols = [col[0] for col in c.description]
            d['gpu'] = [dict(zip(cols, r)) for r in c.fetchall()]

            c.execute("SELECT id, snapshot_id, rig_uuid, timestamp, device, mountpoint, fstype, "
                      "capacity_bytes, usage_pct, temp_c, smart_health, "
                      "read_bytes, write_bytes, read_bytes_delta, write_bytes_delta, "
                      "read_iops, write_iops, read_iops_delta, write_iops_delta, "
                      "busy_time_ms, utilization_pct "
                      "FROM metrics_storagemetric "
                      "WHERE timestamp >= %s AND timestamp < %s "
                      "ORDER BY snapshot_id, device", [start, end])
            cols = [col[0] for col in c.description]
            d['disk'] = [dict(zip(cols, r)) for r in c.fetchall()]

            c.execute("SELECT id, snapshot_id, rig_uuid, timestamp, interface, ipv4, "
                      "link_speed_mbps, rx_bytes, tx_bytes, "
                      "rx_bytes_delta, tx_bytes_delta, rx_errors, tx_errors "
                      "FROM metrics_networkmetric "
                      "WHERE timestamp >= %s AND timestamp < %s "
                      "ORDER BY snapshot_id, interface", [start, end])
            cols = [col[0] for col in c.description]
            d['network'] = [dict(zip(cols, r)) for r in c.fetchall()]

        return d

    def _insert_snapshots(self, snapshots, offset, err_per_snap):
        """Insert snapshots with shifted timestamps. Returns old_id→new_id mapping."""
        id_map = {}
        if not snapshots:
            return id_map

        cols = ('rig_uuid, schema_version, timestamp, '
                'cpu_utilization_pct, cpu_temp_c, cpu_load_avg_json, '
                'mem_used_bytes, mem_free_bytes, mem_cached_bytes, '
                'swap_used_bytes, swap_total_bytes, status, '
                'uptime_s, error_count')

        all_vals = []
        for s in snapshots:
            all_vals.append((
                s['rig_uuid'], s['schema_version'],
                s['timestamp'] - offset,
                s['cpu_utilization_pct'], s['cpu_temp_c'],
                s['cpu_load_avg_json'], s['mem_used_bytes'],
                s['mem_free_bytes'], s['mem_cached_bytes'],
                s['swap_used_bytes'], s['swap_total_bytes'],
                s['status'], s['uptime_s'],
                err_per_snap,
                s['id'],
            ))

        with connection.cursor() as c:
            c.execute("ALTER TABLE metrics_metricsnapshot ADD COLUMN IF NOT EXISTS _bf_old_id BIGINT")

            sql = (f"INSERT INTO metrics_metricsnapshot ({cols}, _bf_old_id) VALUES %s "
                   f"ON CONFLICT (rig_uuid, schema_version, timestamp) DO NOTHING")

            for i in range(0, len(all_vals), BATCH_SIZE):
                batch = all_vals[i:i + BATCH_SIZE]
                execute_values(c, sql, batch, page_size=BATCH_SIZE)

            min_ts = min(s['timestamp'] - offset for s in snapshots)
            max_ts = max(s['timestamp'] - offset for s in snapshots)
            c.execute("""
                SELECT id, _bf_old_id FROM metrics_metricsnapshot
                WHERE timestamp >= %s AND timestamp <= %s AND _bf_old_id IS NOT NULL
            """, [min_ts, max_ts])
            for new_id, old_id in c.fetchall():
                id_map[old_id] = new_id

            c.execute("ALTER TABLE metrics_metricsnapshot DROP COLUMN IF EXISTS _bf_old_id")

        return id_map

    def _insert_child_rows(self, rows, table, id_map, offset):
        if not rows or not id_map:
            return 0

        all_vals = []
        for row in rows:
            old_sid = row['snapshot_id']
            if old_sid not in id_map:
                continue
            new_ts = row['timestamp'] - offset

            if table == 'gpu':
                all_vals.append((
                    id_map[old_sid], row['rig_uuid'], new_ts,
                    row['gpu_index'], row['model'],
                    row['gpu_util_pct'], row['gpu_temp_c'], row['fan_speed_pct'],
                    row['mem_total_mb'], row['mem_used_mb'], row['mem_free_mb'],
                    row['mem_util_pct'], row['power_draw_w'], row['power_limit_w'],
                    row.get('pcie_current_gen'), row.get('pcie_max_gen'),
                    row.get('pcie_current_width'), row.get('pcie_max_width'),
                    row.get('gpu_core_clock_mhz'), row.get('gpu_mem_clock_mhz'),
                ))
            elif table == 'disk':
                all_vals.append((
                    id_map[old_sid], row['rig_uuid'], new_ts,
                    row['device'], row['mountpoint'], row['fstype'],
                    row['capacity_bytes'], row['usage_pct'], row['temp_c'],
                    row['smart_health'],
                    row.get('read_bytes'), row.get('write_bytes'),
                    row.get('read_bytes_delta'), row.get('write_bytes_delta'),
                    row.get('read_iops'), row.get('write_iops'),
                    row.get('read_iops_delta'), row.get('write_iops_delta'),
                    row.get('busy_time_ms'), row.get('utilization_pct'),
                ))
            elif table == 'network':
                all_vals.append((
                    id_map[old_sid], row['rig_uuid'], new_ts,
                    row['interface'], row['ipv4'], row['link_speed_mbps'],
                    row.get('rx_bytes', 0), row.get('tx_bytes', 0),
                    row['rx_bytes_delta'], row['tx_bytes_delta'],
                    row['rx_errors'], row['tx_errors'],
                ))

        if not all_vals:
            return 0

        if table == 'gpu':
            sql = ("INSERT INTO metrics_gpumetric "
                   "(snapshot_id, rig_uuid, timestamp, gpu_index, model, "
                   "gpu_util_pct, gpu_temp_c, fan_speed_pct, "
                   "mem_total_mb, mem_used_mb, mem_free_mb, mem_util_pct, "
                   "power_draw_w, power_limit_w, "
                   "pcie_current_gen, pcie_max_gen, pcie_current_width, pcie_max_width, "
                   "gpu_core_clock_mhz, gpu_mem_clock_mhz) "
                   "VALUES %s ON CONFLICT (rig_uuid, timestamp, gpu_index) DO NOTHING")
        elif table == 'disk':
            sql = ("INSERT INTO metrics_storagemetric "
                   "(snapshot_id, rig_uuid, timestamp, device, mountpoint, fstype, "
                   "capacity_bytes, usage_pct, temp_c, smart_health, "
                   "read_bytes, write_bytes, read_bytes_delta, write_bytes_delta, "
                   "read_iops, write_iops, read_iops_delta, write_iops_delta, "
                   "busy_time_ms, utilization_pct) "
                   "VALUES %s ON CONFLICT (rig_uuid, timestamp, device) DO NOTHING")
        elif table == 'network':
            sql = ("INSERT INTO metrics_networkmetric "
                   "(snapshot_id, rig_uuid, timestamp, interface, ipv4, "
                   "link_speed_mbps, rx_bytes, tx_bytes, "
                   "rx_bytes_delta, tx_bytes_delta, rx_errors, tx_errors) "
                   "VALUES %s ON CONFLICT (rig_uuid, timestamp, interface) DO NOTHING")

        with connection.cursor() as c:
            for i in range(0, len(all_vals), BATCH_SIZE):
                batch = all_vals[i:i + BATCH_SIZE]
                execute_values(c, sql, batch, page_size=BATCH_SIZE)

        return len(all_vals)


