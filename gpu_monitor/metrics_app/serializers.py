import logging
from rest_framework import serializers, status
from django.db import transaction
from django.utils import timezone
from django.core.cache import cache
from .models import MetricSnapshot, GPUMetric, GPUProcessMetric, StorageMetric, NetworkMetric, LatestDockerContainer, LatestSnapshot, RigStatusEvent
from rigs.models import Rig

logger = logging.getLogger(__name__)


class IngestSerializer(serializers.Serializer):
    rig_uuid = serializers.UUIDField()
    rig_name = serializers.CharField(required=False, default='')
    schema_version = serializers.CharField(default='1.5')
    agent_version = serializers.CharField(default='1.1.0')
    timestamp = serializers.DateTimeField()
    metrics = serializers.JSONField(required=False, default=dict)
    motherboard = serializers.JSONField(required=False, default=dict)
    software = serializers.JSONField(required=False, default=dict)
    errors = serializers.ListField(required=False, default=list)

    def validate_schema_version(self, value):
        if value not in ('1.0', '1.1', '1.2', '1.3', '1.4', '1.5', '1.6', '1.7'):
            raise serializers.ValidationError(f"Unsupported schema_version: {value}")
        return value


def process_ingest(rig_uuid, data, owner_id, rig=None):
    """Process an ingestion payload. Returns (response_data, status_code)."""
    serializer = IngestSerializer(data=data)
    if not serializer.is_valid():
        return {'status': 'error', 'message': serializer.errors}, status.HTTP_400_BAD_REQUEST

    validated = serializer.validated_data
    rig_uuid = str(validated['rig_uuid'])
    ts = validated['timestamp']
    schema_version = validated['schema_version']
    metrics_data = validated.get('metrics', {})
    motherboard_data = validated.get('motherboard', {})
    software_data = validated.get('software', {})
    errors_data = validated.get('errors', [])

    # Filter out "no errors" placeholder entries from agents
    # Some agents send [{"source": "kernel", "message": "-- No entries --", "timestamp": ""}]
    # when there are no real errors — these must not be counted or stored
    NO_ERROR_MESSAGES = {'-- No entries --', 'No entries', '', None}
    real_errors = [
        e for e in errors_data
        if e.get('message', '').strip() not in NO_ERROR_MESSAGES
    ]

    cpu = metrics_data.get('cpu', {})
    memory = metrics_data.get('memory', {})
    gpu_list = metrics_data.get('gpus', [])
    gpu_process_list = metrics_data.get('gpu_processes', [])
    storage_list = metrics_data.get('storage', [])
    network_list = metrics_data.get('network', [])
    docker_containers = metrics_data.get('docker_containers', [])

    try:
        with transaction.atomic():
            # Upsert metric snapshot with idempotency
            snapshot, created = MetricSnapshot.objects.update_or_create(
                rig_uuid=rig_uuid,
                schema_version=schema_version,
                timestamp=ts,
                defaults={
                    'cpu_utilization_pct': cpu.get('utilization_pct'),
                    'cpu_temp_c': cpu.get('temp_c'),
                    'cpu_load_avg_json': cpu.get('load_avg', []),
                    'mem_total_bytes': memory.get('total_bytes'),
                    'mem_used_bytes': memory.get('used_bytes'),
                    'mem_free_bytes': memory.get('free_bytes'),
                    'mem_cached_bytes': memory.get('cached_bytes'),
                    'swap_used_bytes': memory.get('swap_used_bytes'),
                    'swap_total_bytes': memory.get('swap_total_bytes'),
                    'status': rig.status if rig else None,
                    'uptime_s': software_data.get('uptime_s'),
                    'error_count': len(real_errors),
                },
            )

            # Store per-GPU metrics (with uuid, model, mem_total — all per-row for tracking)
            for idx, gpu in enumerate(gpu_list):
                GPUMetric.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    gpu_index=idx,
                    defaults={
                        'snapshot': snapshot,
                        'model': gpu.get('model', ''),
                        'gpu_util_pct': gpu.get('gpu_util_pct'),
                        'gpu_temp_c': gpu.get('temp_c'),
                        'fan_speed_pct': gpu.get('fan_speed_pct'),
                        'mem_total_mb': gpu.get('mem_total_mb'),
                        'mem_used_mb': gpu.get('mem_used_mb'),
                        'mem_free_mb': gpu.get('mem_free_mb'),
                        'mem_util_pct': gpu.get('mem_util_pct'),
                        'power_draw_w': gpu.get('power_draw_w'),
                        'power_limit_w': gpu.get('power_limit_w'),
                        'pcie_current_gen': gpu.get('pcie_current_gen'),
                        'pcie_max_gen': gpu.get('pcie_max_gen'),
                        'pcie_current_width': gpu.get('pcie_current_width'),
                        'pcie_max_width': gpu.get('pcie_max_width'),
                        'gpu_core_clock_mhz': gpu.get('gpu_core_clock_mhz'),
                        'gpu_mem_clock_mhz': gpu.get('gpu_mem_clock_mhz'),
                    },
                )

            # Store per-GPU process metrics
            # Delete old process records for this rig first — we only care about
            # the latest snapshot, not historical process data
            GPUProcessMetric.objects.filter(rig_uuid=rig_uuid).delete()
            for proc in gpu_process_list:
                GPUProcessMetric.objects.create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    snapshot=snapshot,
                    gpu_index=proc.get('gpu_index', 0),
                    pid=proc.get('pid'),
                    process_name=proc.get('name', '')[:500],
                    type=proc.get('type', ''),
                    gpu_mem_mb=proc.get('gpu_mem_mb'),
                )

            # Store per-disk metrics with I/O delta calculation
            # First pass: compute deltas and upsert; store delta values for LatestSnapshot
            disk_deltas = {}  # device_name -> {read_bytes_delta, write_bytes_delta, ...}
            for disk in storage_list:
                device_name = disk.get('device', '')
                new_read_bytes = disk.get('read_bytes')
                new_write_bytes = disk.get('write_bytes')
                new_read_iops = disk.get('read_iops')
                new_write_iops = disk.get('write_iops')
                new_busy_time_ms = disk.get('busy_time_ms')

                # Calculate deltas by comparing with previous reading for this device
                read_bytes_delta = None
                write_bytes_delta = None
                read_iops_delta = None
                write_iops_delta = None
                utilization_pct = None
                try:
                    prev = StorageMetric.objects.filter(
                        rig_uuid=rig_uuid,
                        device=device_name,
                    ).order_by('-timestamp').first()
                    if prev:
                        # Byte deltas
                        if new_read_bytes is not None and prev.read_bytes is not None:
                            read_bytes_delta = new_read_bytes - prev.read_bytes
                            if read_bytes_delta < 0:
                                read_bytes_delta = new_read_bytes  # counter wraparound
                        if new_write_bytes is not None and prev.write_bytes is not None:
                            write_bytes_delta = new_write_bytes - prev.write_bytes
                            if write_bytes_delta < 0:
                                write_bytes_delta = new_write_bytes
                        # IOPS deltas
                        if new_read_iops is not None and prev.read_iops is not None:
                            read_iops_delta = new_read_iops - prev.read_iops
                            if read_iops_delta < 0:
                                read_iops_delta = new_read_iops
                        if new_write_iops is not None and prev.write_iops is not None:
                            write_iops_delta = new_write_iops - prev.write_iops
                            if write_iops_delta < 0:
                                write_iops_delta = new_write_iops
                        # Busy time delta → utilization %
                        if new_busy_time_ms is not None and prev.busy_time_ms is not None:
                            busy_time_delta_ms = new_busy_time_ms - prev.busy_time_ms
                            if busy_time_delta_ms < 0:
                                busy_time_delta_ms = new_busy_time_ms
                            time_elapsed_s = (ts - prev.timestamp).total_seconds()
                            if time_elapsed_s > 0:
                                utilization_pct = round(
                                    busy_time_delta_ms / (time_elapsed_s * 1000) * 100, 2
                                )
                                utilization_pct = max(0.0, min(100.0, utilization_pct))
                except Exception:
                    pass

                # Store delta values for LatestSnapshot JSON arrays
                disk_deltas[device_name] = {
                    'read_bytes_delta': read_bytes_delta,
                    'write_bytes_delta': write_bytes_delta,
                    'read_iops_delta': read_iops_delta,
                    'write_iops_delta': write_iops_delta,
                    'utilization_pct': utilization_pct,
                }

                StorageMetric.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    device=device_name,
                    defaults={
                        'snapshot': snapshot,
                        'mountpoint': disk.get('mountpoint', ''),
                        'fstype': disk.get('fstype', ''),
                        'capacity_bytes': disk.get('capacity_bytes'),
                        'usage_pct': disk.get('usage_pct'),
                        'temp_c': disk.get('temp_c'),
                        'smart_health': disk.get('smart_health', ''),
                        'read_bytes': new_read_bytes,
                        'write_bytes': new_write_bytes,
                        'read_iops': new_read_iops,
                        'write_iops': new_write_iops,
                        'busy_time_ms': new_busy_time_ms,
                        'read_bytes_delta': read_bytes_delta,
                        'write_bytes_delta': write_bytes_delta,
                        'read_iops_delta': read_iops_delta,
                        'write_iops_delta': write_iops_delta,
                        'utilization_pct': utilization_pct,
                    },
                )

            # Store per-interface metrics with traffic delta calculation
            for iface in network_list:
                iface_name = iface.get('interface', '')
                new_rx = iface.get('rx_bytes')
                new_tx = iface.get('tx_bytes')

                # Calculate deltas by comparing with previous reading for this interface
                rx_delta = None
                tx_delta = None
                try:
                    prev = NetworkMetric.objects.filter(
                        rig_uuid=rig_uuid,
                        interface=iface_name,
                    ).order_by('-timestamp').first()
                    if prev and new_rx is not None and new_tx is not None:
                        rx_delta = new_rx - prev.rx_bytes if prev.rx_bytes else None
                        tx_delta = new_tx - prev.tx_bytes if prev.tx_bytes else None
                        # Handle counter wraparound (shouldn't happen with 64-bit counters, but be safe)
                        if rx_delta is not None and rx_delta < 0:
                            rx_delta = new_rx
                        if tx_delta is not None and tx_delta < 0:
                            tx_delta = new_tx
                except Exception:
                    pass

                NetworkMetric.objects.update_or_create(
                    rig_uuid=rig_uuid,
                    timestamp=ts,
                    interface=iface_name,
                    defaults={
                        'snapshot': snapshot,
                        'ipv4': iface.get('ipv4', ''),
                        'link_speed_mbps': iface.get('link_speed_mbps'),
                        'rx_bytes': new_rx,
                        'tx_bytes': new_tx,
                        'rx_bytes_delta': rx_delta,
                        'tx_bytes_delta': tx_delta,
                        'rx_errors': iface.get('rx_errors'),
                        'tx_errors': iface.get('tx_errors'),
                    },
                )

            # Store latest container snapshot (for Live Metrics display)
            # Delete-before-insert pattern: remove all old rows for this rig first
            LatestDockerContainer.objects.filter(rig_uuid=rig_uuid).delete()
            for container in docker_containers:
                container_id = container.get('container_id')
                if not container_id:
                    continue
                LatestDockerContainer.objects.create(
                    rig_uuid=rig_uuid,
                    container_id=container_id,
                    name=container.get('name', ''),
                    image=container.get('image', ''),
                    status=container.get('status', ''),
                    created=container.get('created', ''),
                    status_text=container.get('status_text', ''),
                )

            # Build GPU summary data for LatestSnapshot (fast dashboard access)
            gpu_uuids = []
            gpu_models = []
            gpu_temps = []
            gpu_utils = []
            gpu_fans = []
            gpu_core_clocks = []
            gpu_mem_clocks = []
            gpu_mem_used = []
            gpu_mem_total = []
            gpu_mem_util_pcts = []
            gpu_mem_free = []
            gpu_power_draws = []
            gpu_power_limits = []
            gpu_pcie_gen = []
            gpu_pcie_max_gen = []
            gpu_pcie_width = []
            gpu_pcie_max_width = []
            for idx, gpu in enumerate(gpu_list):
                gpu_uuids.append(gpu.get('uuid', ''))
                gpu_models.append(gpu.get('model', ''))
                gpu_temps.append(gpu.get('temp_c'))
                gpu_utils.append(gpu.get('gpu_util_pct'))
                gpu_fans.append(gpu.get('fan_speed_pct'))
                gpu_core_clocks.append(gpu.get('gpu_core_clock_mhz'))
                gpu_mem_clocks.append(gpu.get('gpu_mem_clock_mhz'))
                gpu_mem_used.append(gpu.get('mem_used_mb'))
                gpu_mem_total.append(gpu.get('mem_total_mb'))
                gpu_mem_util_pcts.append(gpu.get('mem_util_pct'))
                gpu_mem_free.append(gpu.get('mem_free_mb'))
                gpu_power_draws.append(gpu.get('power_draw_w'))
                gpu_power_limits.append(gpu.get('power_limit_w'))
                gpu_pcie_gen.append(gpu.get('pcie_current_gen'))
                gpu_pcie_max_gen.append(gpu.get('pcie_max_gen'))
                gpu_pcie_width.append(gpu.get('pcie_current_width'))
                gpu_pcie_max_width.append(gpu.get('pcie_max_width'))

            # Build storage summary data for LatestSnapshot
            storage_devices = []
            storage_fstypes = []
            storage_mountpoints = []
            storage_capacities = []
            storage_usage_pcts = []
            storage_temps = []
            storage_smart = []
            storage_read_bytes_delta = []
            storage_write_bytes_delta = []
            storage_read_iops_delta = []
            storage_write_iops_delta = []
            storage_utilization_pcts = []
            # Cumulative totals since boot (raw counters from agent)
            storage_read_bytes_total = []
            storage_write_bytes_total = []
            storage_read_iops_total = []
            storage_write_iops_total = []
            for disk in storage_list:
                device_name = disk.get('device', '')
                storage_devices.append(device_name)
                storage_fstypes.append(disk.get('fstype', ''))
                storage_mountpoints.append(disk.get('mountpoint', ''))
                storage_capacities.append(disk.get('capacity_bytes'))
                storage_usage_pcts.append(disk.get('usage_pct'))
                storage_temps.append(disk.get('temp_c'))
                storage_smart.append(disk.get('smart_health', ''))
                # Use computed deltas from the ingest loop
                deltas = disk_deltas.get(device_name, {})
                storage_read_bytes_delta.append(deltas.get('read_bytes_delta'))
                storage_write_bytes_delta.append(deltas.get('write_bytes_delta'))
                storage_read_iops_delta.append(deltas.get('read_iops_delta'))
                storage_write_iops_delta.append(deltas.get('write_iops_delta'))
                storage_utilization_pcts.append(deltas.get('utilization_pct'))
                # Cumulative totals from raw agent payload
                storage_read_bytes_total.append(disk.get('read_bytes'))
                storage_write_bytes_total.append(disk.get('write_bytes'))
                storage_read_iops_total.append(disk.get('read_iops'))
                storage_write_iops_total.append(disk.get('write_iops'))

            # Build network summary data for LatestSnapshot
            network_interfaces = []
            network_ipv4s = []
            network_speeds = []
            network_rx_bytes = []
            network_tx_bytes = []
            network_rx_errors = []
            network_tx_errors = []
            for iface in network_list:
                network_interfaces.append(iface.get('interface', ''))
                network_ipv4s.append(iface.get('ipv4', ''))
                network_speeds.append(iface.get('link_speed_mbps'))
                network_rx_bytes.append(iface.get('rx_bytes'))
                network_tx_bytes.append(iface.get('tx_bytes'))
                network_rx_errors.append(iface.get('rx_errors', 0))
                network_tx_errors.append(iface.get('tx_errors', 0))

            # Update latest snapshot (denormalized)
            LatestSnapshot.objects.update_or_create(
                rig_uuid=rig_uuid,
                defaults={
                    'schema_version': schema_version,
                    'timestamp': ts,
                    # CPU dynamic
                    'cpu_utilization_pct': cpu.get('utilization_pct'),
                    'cpu_temp_c': cpu.get('temp_c'),
                    'cpu_load_avg_json': cpu.get('load_avg', []),
                    # CPU static (updated in-place — can change on CPU swap)
                    'cpu_model': cpu.get('model', ''),
                    'cpu_physical_cores': cpu.get('physical_cores'),
                    'cpu_logical_cores': cpu.get('logical_cores'),
                    # Memory dynamic
                    'mem_used_bytes': memory.get('used_bytes'),
                    'mem_free_bytes': memory.get('free_bytes'),
                    'mem_cached_bytes': memory.get('cached_bytes'),
                    'swap_used_bytes': memory.get('swap_used_bytes'),
                    # Memory static (updated in-place — can change on RAM upgrade)
                    'mem_total_bytes': memory.get('total_bytes'),
                    'swap_total_bytes': memory.get('swap_total_bytes'),
                    # Motherboard (updated in-place — can change on mobo swap)
                    'motherboard_json': motherboard_data,
                    # Software (updated in-place — can change on OS/driver update)
                    'software_json': software_data,
                    'agent_version': validated.get('agent_version', '1.0.0'),
                    # GPU
                    'gpu_count': len(gpu_list),
                    'gpu_uuids_json': gpu_uuids,
                    'gpu_models_json': gpu_models,
                    'gpu_temps_json': gpu_temps,
                    'gpu_utils_json': gpu_utils,
                    'gpu_fans_json': gpu_fans,
                    'gpu_core_clocks_json': gpu_core_clocks,
                    'gpu_mem_clocks_json': gpu_mem_clocks,
                    'gpu_mem_used_json': gpu_mem_used,
                    'gpu_mem_total_json': gpu_mem_total,
                    'gpu_mem_util_pcts_json': gpu_mem_util_pcts,
                    'gpu_mem_free_json': gpu_mem_free,
                    'gpu_power_draws_json': gpu_power_draws,
                    'gpu_power_limits_json': gpu_power_limits,
                    'gpu_pcie_gen_json': gpu_pcie_gen,
                    'gpu_pcie_max_gen_json': gpu_pcie_max_gen,
                    'gpu_pcie_width_json': gpu_pcie_width,
                    'gpu_pcie_max_width_json': gpu_pcie_max_width,
                    'storage_count': len(storage_list),
                    'storage_devices_json': storage_devices,
                    'storage_fstypes_json': storage_fstypes,
                    'storage_mountpoints_json': storage_mountpoints,
                    'storage_capacities_json': storage_capacities,
                    'storage_usage_pcts_json': storage_usage_pcts,
                    'storage_temps_json': storage_temps,
                    'storage_smart_json': storage_smart,
                    'storage_read_bytes_delta_json': storage_read_bytes_delta,
                    'storage_write_bytes_delta_json': storage_write_bytes_delta,
                    'storage_read_iops_delta_json': storage_read_iops_delta,
                    'storage_write_iops_delta_json': storage_write_iops_delta,
                    'storage_utilization_pcts_json': storage_utilization_pcts,
                    'storage_read_bytes_total_json': storage_read_bytes_total,
                    'storage_write_bytes_total_json': storage_write_bytes_total,
                    'storage_read_iops_total_json': storage_read_iops_total,
                    'storage_write_iops_total_json': storage_write_iops_total,
                    'network_count': len(network_list),
                    'network_interfaces_json': network_interfaces,
                    'network_ipv4s_json': network_ipv4s,
                    'network_speeds_json': network_speeds,
                    'network_rx_bytes_json': network_rx_bytes,
                    'network_tx_bytes_json': network_tx_bytes,
                    'network_rx_errors_json': network_rx_errors,
                    'network_tx_errors_json': network_tx_errors,
                },
            )
            # Invalidate cached snapshot so next read gets fresh data
            cache.delete(f'lsnap_{rig_uuid}')

            # Track rig status transitions
            if rig:
                previous_status = rig.status
                current_status = Rig.Status.ONLINE  # Heartbeat always means online
                if previous_status != current_status:
                    RigStatusEvent.objects.create(
                        rig_uuid=rig_uuid,
                        status=current_status,
                        previous_status=previous_status,
                    )

            # Update latest error text on Rig (like motherboard_json — updated in place)
            if real_errors and rig:
                rig.latest_errors_json = [
                    {'source': e.get('source', ''), 'message': e.get('message', '')[:200], 'timestamp': e.get('timestamp', '')}
                    for e in real_errors[:10]
                ]
                rig.save(update_fields=['latest_errors_json'])
            elif rig:
                # No real errors — clear the error list
                rig.latest_errors_json = []
                rig.save(update_fields=['latest_errors_json'])

            http_status = status.HTTP_200_OK if created else status.HTTP_202_ACCEPTED
            status_label = 'new' if created else 'duplicate'

            return {
                'status': status_label,
                'message': f'Payload {status_label}',
                'next_expected': '',
            }, http_status

    except Exception as e:
        logger.exception("Ingestion failed for rig %s", rig_uuid)
        return {
            'status': 'error',
            'message': f'Internal error: {str(e)}',
        }, status.HTTP_500_INTERNAL_SERVER_ERROR
