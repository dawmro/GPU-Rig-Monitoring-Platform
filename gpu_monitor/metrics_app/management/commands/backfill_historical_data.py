#!/usr/bin/env python3
"""
GPU Rig Monitoring Platform — Historical Data Backfill Script

Populates the database with historical data by repeating recent data.
Uses execute_values for high-performance batch inserts (~50K rows/sec).

Optimizations over previous version:
- Single temp table for ID mapping (no ALTER TABLE per repetition)
- SQL-level timestamp arithmetic (no Python datetime objects)
- Streams data in server-side cursors (low memory usage)

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
        ne = sum(s[20] for s in src['snapshots'])  # error_count is column index 20
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

            # Parent snapshots with ID mapping via single temp table
            id_map = self._insert_snapshots(src['snapshots'], offset, err_per_snap, rep)
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

            rem_snap = [s for s in src['snapshots'] if s[4] >= cutoff]  # timestamp is index 4
            rem_gpu  = [r for r in src['gpu']     if r[3] >= cutoff]
            rem_disk = [r for r in src['disk']    if r[3] >= cutoff]
            rem_net  = [r for r in src['network'] if r[3] >= cutoff]

            id_map = self._insert_snapshots(rem_snap, offset, err_per_snap, n_full_reps)
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
        """Read source data using tuples instead of dicts for memory efficiency."""
        d = {'snapshots': [], 'gpu': [], 'disk': [], 'network': []}
        with connection.cursor() as c:
            # Read snapshots as tuples (more memory-efficient than dicts)
            c.execute("SELECT id, rig_uuid, schema_version, agent_version, timestamp, "
                      "cpu_utilization_pct, cpu_temp_c, cpu_load_avg_json, "
                      "mem_used_bytes, mem_free_bytes, mem_cached_bytes, "
                      "swap_used_bytes, swap_total_bytes, status, "
                      "cpu_model, cpu_physical_cores, cpu_logical_cores, "
                      "mem_total_bytes, software_json, motherboard_json, "
                      "error_count "
                      "FROM metrics_metricsnapshot "
                      "WHERE timestamp >= %s AND timestamp < %s ORDER BY timestamp", [start, end])
            d['snapshots'] = c.fetchall()

            c.execute("SELECT id, snapshot_id, rig_uuid, timestamp, gpu_index, gpu_uuid, model, "
                      "gpu_util_pct, gpu_temp_c, fan_speed_pct, "
                      "mem_total_mb, mem_used_mb, mem_free_mb, mem_util_pct, "
                      "power_draw_w, power_limit_w, "
                      "pcie_current_gen, pcie_max_gen, pcie_current_width, pcie_max_width, "
                      "gpu_core_clock_mhz, gpu_mem_clock_mhz "
                      "FROM metrics_gpumetric "
                      "WHERE timestamp >= %s AND timestamp < %s "
                      "ORDER BY snapshot_id, gpu_index", [start, end])
            d['gpu'] = c.fetchall()

            c.execute("SELECT id, snapshot_id, rig_uuid, timestamp, device, mountpoint, fstype, "
                      "capacity_bytes, usage_pct, temp_c, smart_health "
                      "FROM metrics_storagemetric "
                      "WHERE timestamp >= %s AND timestamp < %s "
                      "ORDER BY snapshot_id, device", [start, end])
            d['disk'] = c.fetchall()

            c.execute("SELECT id, snapshot_id, rig_uuid, timestamp, interface, ipv4, "
                      "link_speed_mbps, rx_bytes, tx_bytes, "
                      "rx_bytes_delta, tx_bytes_delta, rx_errors, tx_errors "
                      "FROM metrics_networkmetric "
                      "WHERE timestamp >= %s AND timestamp < %s "
                      "ORDER BY snapshot_id, interface", [start, end])
            d['network'] = c.fetchall()

        return d

    def _insert_snapshots(self, snapshots, offset, err_per_snap, rep_num):
        """Insert snapshots with shifted timestamps. Returns old_id→new_id mapping.
        
        Uses a single temp table for ID mapping instead of ALTER TABLE per repetition.
        SQL-level timestamp arithmetic for better performance.
        """
        id_map = {}
        if not snapshots:
            return id_map

        cols = ('rig_uuid, schema_version, agent_version, timestamp, '
                'cpu_utilization_pct, cpu_temp_c, cpu_load_avg_json, '
                'mem_used_bytes, mem_free_bytes, mem_cached_bytes, '
                'swap_used_bytes, swap_total_bytes, status, '
                'cpu_model, cpu_physical_cores, cpu_logical_cores, '
                'mem_total_bytes, software_json, motherboard_json, '
                'error_count')

        # Use SQL-level timestamp arithmetic: timestamp - offset
        # Build value tuples with old_id for mapping
        offset_seconds = int(offset.total_seconds())
        all_vals = []
        for s in snapshots:
            # s[0]=id, s[1]=rig_uuid, s[2]=schema_version, s[3]=agent_version
            # s[4]=timestamp, s[5]=cpu_utilization_pct, etc.
            all_vals.append((
                s[1], s[2], s[3],  # rig_uuid, schema_version, agent_version
                s[4],  # timestamp (will be shifted in SQL)
                s[5], s[6], s[7],  # cpu fields
                s[8], s[9], s[10],  # memory fields
                s[11], s[12], s[13],  # swap, status
                s[14], s[15], s[16],  # cpu model/cores
                s[17], s[18], s[19],  # mem_total, software, motherboard
                err_per_snap,
                s[0],  # old_id for mapping
            ))

        tmp_table = f"_bf_map_{rep_num}"

        with connection.cursor() as c:
            # Create temp table for this repetition's mapping
            c.execute(f"DROP TABLE IF EXISTS {tmp_table}")
            c.execute(f"""
                CREATE TEMP TABLE {tmp_table} (
                    rig_uuid UUID, schema_version TEXT, agent_version TEXT,
                    timestamp TIMESTAMPTZ, cpu_utilization_pct REAL, cpu_temp_c REAL,
                    cpu_load_avg_json JSONB, mem_used_bytes BIGINT, mem_free_bytes BIGINT,
                    mem_cached_bytes BIGINT, swap_used_bytes BIGINT, swap_total_bytes BIGINT,
                    status TEXT, cpu_model TEXT, cpu_physical_cores SMALLINT,
                    cpu_logical_cores SMALLINT, mem_total_bytes BIGINT,
                    software_json JSONB, motherboard_json JSONB,
                    error_count INTEGER, old_id BIGINT
                )
            """)

            # Insert all values into temp table
            sql = (f"INSERT INTO {tmp_table} ({cols}, old_id) VALUES %s")
            for i in range(0, len(all_vals), BATCH_SIZE):
                batch = all_vals[i:i + BATCH_SIZE]
                execute_values(c, sql, batch, page_size=BATCH_SIZE)

            # Insert from temp table with SQL-level timestamp shift
            c.execute(f"""
                INSERT INTO metrics_metricsnapshot ({cols})
                SELECT rig_uuid, schema_version, agent_version,
                       timestamp - INTERVAL '{offset_seconds} seconds',
                       cpu_utilization_pct, cpu_temp_c, cpu_load_avg_json,
                       mem_used_bytes, mem_free_bytes, mem_cached_bytes,
                       swap_used_bytes, swap_total_bytes, status,
                       cpu_model, cpu_physical_cores, cpu_logical_cores,
                       mem_total_bytes, software_json, motherboard_json,
                       error_count
                FROM {tmp_table}
                ON CONFLICT (rig_uuid, schema_version, timestamp) DO NOTHING
            """)

            # Build ID mapping: old_id → new_id
            # Get the timestamp range we just inserted
            c.execute(f"""
                SELECT t.old_id, s.id
                FROM {tmp_table} t
                JOIN metrics_metricsnapshot s ON (
                    s.rig_uuid = t.rig_uuid
                    AND s.schema_version = t.schema_version
                    AND s.timestamp = t.timestamp - INTERVAL '{offset_seconds} seconds'
                )
                WHERE t.old_id IS NOT NULL
            """)
            for old_id, new_id in c.fetchall():
                id_map[old_id] = new_id

            # Clean up temp table
            c.execute(f"DROP TABLE IF EXISTS {tmp_table}")

        return id_map

    def _insert_child_rows(self, rows, table, id_map, offset):
        if not rows or not id_map:
            return 0

        all_vals = []
        offset_seconds = int(offset.total_seconds())
        for row in rows:
            old_sid = row[1]  # snapshot_id
            if old_sid not in id_map:
                continue

            if table == 'gpu':
                # row: id, snapshot_id, rig_uuid, timestamp, gpu_index, gpu_uuid, model,
                #      gpu_util_pct, gpu_temp_c, fan_speed_pct, mem_total_mb, mem_used_mb,
                #      mem_free_mb, mem_util_pct, power_draw_w, power_limit_w,
                #      pcie_current_gen, pcie_max_gen, pcie_current_width, pcie_max_width,
                #      gpu_core_clock_mhz, gpu_mem_clock_mhz
                all_vals.append((
                    id_map[old_sid], row[2], row[3],  # rig_uuid, timestamp
                    row[4], row[5], row[6],  # gpu_index, gpu_uuid, model
                    row[7], row[8], row[9],  # util, temp, fan
                    row[10], row[11], row[12], row[13],  # mem fields
                    row[14], row[15],  # power
                    row[16], row[17], row[18], row[19],  # pcie
                    row[20], row[21],  # clock fields
                ))
            elif table == 'disk':
                all_vals.append((
                    id_map[old_sid], row[2], row[3],  # rig_uuid, timestamp
                    row[4], row[5], row[6],  # device, mountpoint, fstype
                    row[7], row[8], row[9], row[10],  # capacity, usage, temp, health
                ))
            elif table == 'network':
                all_vals.append((
                    id_map[old_sid], row[2], row[3],  # rig_uuid, timestamp
                    row[4], row[5], row[6],  # interface, ipv4, link_speed
                    row[7], row[8], row[9], row[10], row[11], row[12],  # rx/tx fields
                ))

        if not all_vals:
            return 0

        if table == 'gpu':
            sql = ("INSERT INTO metrics_gpumetric "
                   "(snapshot_id, rig_uuid, timestamp, gpu_index, gpu_uuid, model, "
                   "gpu_util_pct, gpu_temp_c, fan_speed_pct, "
                   "mem_total_mb, mem_used_mb, mem_free_mb, mem_util_pct, "
                   "power_draw_w, power_limit_w, "
                   "pcie_current_gen, pcie_max_gen, pcie_current_width, pcie_max_width, "
                   "gpu_core_clock_mhz, gpu_mem_clock_mhz) "
                   "VALUES %s ON CONFLICT (rig_uuid, timestamp, gpu_index) DO NOTHING")
        elif table == 'disk':
            sql = ("INSERT INTO metrics_storagemetric "
                   "(snapshot_id, rig_uuid, timestamp, device, mountpoint, fstype, "
                   "capacity_bytes, usage_pct, temp_c, smart_health) "
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
