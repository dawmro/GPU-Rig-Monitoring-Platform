from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Set up TimescaleDB hypertables and retention policies'

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            # Convert metrics_metricsnapshot to hypertable
            try:
                cursor.execute("""
                    SELECT create_hypertable(
                        'metrics_metricsnapshot',
                        'timestamp',
                        if_not_exists => TRUE,
                        chunk_time_interval => INTERVAL '1 day'
                    );
                """)
                self.stdout.write(self.style.SUCCESS('Created hypertable: metrics_metricsnapshot'))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'Hypertable setup: {e}'))

            # Retention policy: drop raw data after 7 days
            try:
                cursor.execute("""
                    SELECT add_retention_policy(
                        'metrics_metricsnapshot',
                        drop_after => INTERVAL '7 days',
                        if_not_exists => TRUE
                    );
                """)
                self.stdout.write(self.style.SUCCESS('Added 7-day retention policy'))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'Retention policy: {e}'))

            # Continuous aggregate: hourly
            try:
                cursor.execute("""
                    CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_hourly_agg
                    WITH (timescaledb.continuous) AS
                    SELECT
                        rig_uuid,
                        time_bucket('1 hour', timestamp) AS bucket,
                        AVG(cpu_utilization_pct) AS avg_cpu_util,
                        MAX(cpu_utilization_pct) AS max_cpu_util,
                        AVG(cpu_temp_c) AS avg_cpu_temp,
                        MAX(cpu_temp_c) AS max_cpu_temp,
                        AVG(mem_used_bytes) AS avg_mem_used,
                        MAX(mem_used_bytes) AS max_mem_used
                    FROM metrics_metricsnapshot
                    GROUP BY rig_uuid, bucket
                    WITH NO DATA;
                """)
                self.stdout.write(self.style.SUCCESS('Created hourly continuous aggregate'))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'Hourly aggregate: {e}'))

            # Refresh policy for hourly aggregate
            try:
                cursor.execute("""
                    SELECT add_continuous_aggregate_policy(
                        'metrics_hourly_agg',
                        start_offset => INTERVAL '3 hours',
                        end_offset => INTERVAL '1 hour',
                        schedule_interval => INTERVAL '1 hour',
                        if_not_exists => TRUE
                    );
                """)
                self.stdout.write(self.style.SUCCESS('Added hourly refresh policy'))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'Refresh policy: {e}'))

        self.stdout.write(self.style.SUCCESS('TimescaleDB setup complete'))
