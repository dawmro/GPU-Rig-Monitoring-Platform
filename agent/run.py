#!/usr/bin/env python3
"""
GPU Rig Monitoring Agent v1.0.0

Collects hardware/software metrics and sends them to the monitoring server.
Designed to run via cron every 60 seconds.

Usage:
    python3 run.py

Config file: /etc/monitoring-agent/config.yaml
"""

import os
import sys
import json
import signal
import logging
import logging.handlers
import platform
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
import requests

__version__ = '1.0.0'
__schema_version__ = '1.0'

# ── Config ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIG_PATH = '/etc/monitoring-agent/config.yaml'

def load_config(path=DEFAULT_CONFIG_PATH):
    """Load and validate configuration."""
    with open(path, 'r') as f:
        config = yaml.safe_load(f) or {}

    required = ['api_key', 'server_endpoint']
    for field in required:
        if not config.get(field):
            print(f"ERROR: Missing required config field: {field}", file=sys.stderr)
            sys.exit(2)

    # Auto-generate UUID on first run
    if config.get('rig_uuid') == 'auto' or not config.get('rig_uuid'):
        config['rig_uuid'] = str(uuid.uuid4())
        try:
            config_path = Path(path)
            existing = yaml.safe_load(config_path.read_text()) or {}
            existing['rig_uuid'] = config['rig_uuid']
            config_path.write_text(yaml.dump(existing))
        except Exception:
            pass

    # Set default rig_name from config or hostname
    if not config.get('rig_name'):
        config['rig_name'] = platform.node() or 'Unnamed Rig'

    return config


# ── Logging ─────────────────────────────────────────────────────────────────

def setup_logging(debug=False):
    log_dir = Path('/var/log/monitoring-agent')
    log_dir.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if debug else logging.INFO
    fmt = '{"ts":"%(asctime)s","level":"%(levelname)s","module":"%(name)s","msg":"%(message)s"}'

    handler = logging.handlers.RotatingFileHandler(
        log_dir / 'agent.log', maxBytes=10*1024*1024, backupCount=3
    )
    handler.setFormatter(logging.Formatter(fmt))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    root.addHandler(console)


# ── Metric Collectors ───────────────────────────────────────────────────────

def collect_cpu():
    """Collect CPU metrics."""
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count_phys = psutil.cpu_count(logical=False)
        cpu_count_log = psutil.cpu_count(logical=True)
        load_avg = os.getloadavg()

        temp_c = None
        try:
            import psutil
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    if entries:
                        temp_c = entries[0].current
                        break
        except Exception:
            pass

        model = 'Unknown'
        try:
            import cpuinfo
            info = cpuinfo.get_cpu_info()
            model = info.get('brand_raw', 'Unknown')
        except Exception:
            pass

        return {
            'model': model,
            'physical_cores': cpu_count_phys,
            'logical_cores': cpu_count_log,
            'load_avg': list(load_avg),
            'utilization_pct': cpu_percent,
            'temp_c': temp_c,
        }
    except Exception as e:
        logging.getLogger('cpu').warning('CPU collection failed: %s', e)
        return {}


def collect_memory():
    """Collect memory metrics."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            'total_bytes': vm.total,
            'used_bytes': vm.used,
            'free_bytes': vm.available,
            'cached_bytes': getattr(vm, 'cached', None),
            'swap_used_bytes': swap.used,
            'swap_total_bytes': swap.total,
        }
    except Exception as e:
        logging.getLogger('memory').warning('Memory collection failed: %s', e)
        return {}


def collect_motherboard():
    """Collect motherboard info."""
    result = {}
    try:
        for field, path in [
            ('manufacturer', '/sys/class/dmi/id/board_vendor'),
            ('model', '/sys/class/dmi/id/board_name'),
            ('bios_version', '/sys/class/dmi/id/bios_version'),
        ]:
            try:
                result[field] = Path(path).read_text().strip()
            except Exception:
                result[field] = 'unknown'
    except Exception:
        pass
    return result


def collect_storage():
    """Collect storage metrics."""
    try:
        import psutil
        disks = []
        for part in psutil.disk_partitions():
            if part.fstype in ('squashfs', 'tmpfs', 'devtmpfs'):
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disk = {
                    'device': part.device,
                    'mountpoint': part.mountpoint,
                    'fstype': part.fstype,
                    'capacity_bytes': usage.total,
                    'usage_pct': round(usage.percent, 1),
                }
                # Try SMART
                try:
                    out = subprocess.run(
                        ['sudo', 'smartctl', '-a', part.device],
                        capture_output=True, text=True, timeout=5
                    )
                    for line in out.stdout.splitlines():
                        if 'Temperature' in line and 'Celsius' in line:
                            parts_w = line.split()
                            for i, w in enumerate(parts_w):
                                if w.replace('.', '').isdigit() and i > 0:
                                    disk['temp_c'] = float(w)
                                    break
                except Exception:
                    pass
                disks.append(disk)
            except PermissionError:
                continue
        return disks
    except Exception as e:
        logging.getLogger('storage').warning('Storage collection failed: %s', e)
        return []


def collect_network():
    """Collect network metrics."""
    try:
        import psutil
        interfaces = []
        stats = psutil.net_io_counters(pernic=True)
        addrs = psutil.net_if_addrs()
        for iface, snic in stats.items():
            if iface == 'lo':
                continue
            entry = {
                'interface': iface,
                'rx_bytes': snic.bytes_recv,
                'tx_bytes': snic.bytes_sent,
                'rx_errors': snic.errin,
                'tx_errors': snic.errout,
            }
            # IPv4
            if iface in addrs:
                for a in addrs[iface]:
                    if a.family.name == 'AF_INET':
                        entry['ipv4'] = a.address
                        break
            # Link speed
            try:
                speed_path = Path(f'/sys/class/net/{iface}/speed')
                speed = int(speed_path.read_text().strip())
                if speed > 0:
                    entry['link_speed_mbps'] = speed
            except Exception:
                pass
            interfaces.append(entry)
        return interfaces
    except Exception as e:
        logging.getLogger('network').warning('Network collection failed: %s', e)
        return []


def collect_gpus():
    """Collect GPU metrics via pynvml."""
    try:
        import pynvml
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        gpus = []
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            try:
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                temp = None
            try:
                fan = pynvml.nvmlDeviceGetFanSpeed(handle)
            except Exception:
                fan = None
            try:
                power = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
                power_limit = pynvml.nvmlDeviceGetPowerManagementDefaultLimit(handle) / 1000.0
            except Exception:
                power = None
                power_limit = None

            gpu = {
                'uuid': pynvml.nvmlDeviceGetUUID(handle),
                'model': pynvml.nvmlDeviceGetName(handle),
                'mem_total_mb': info.total // (1024 * 1024),
                'mem_used_mb': info.used // (1024 * 1024),
                'mem_free_mb': info.free // (1024 * 1024),
                'mem_util_pct': round(info.used / info.total * 100, 1) if info.total else None,
                'gpu_util_pct': util.gpu,
                'temp_c': temp,
                'fan_speed_pct': fan,
                'power_draw_w': power,
                'power_limit_w': power_limit,
            }
            gpus.append(gpu)
        pynvml.nvmlShutdown()
        return gpus
    except Exception as e:
        logging.getLogger('gpu').warning('GPU collection failed: %s', e)
        return []


def collect_docker():
    """Collect Docker container info."""
    try:
        import docker
        client = docker.from_env()
        containers = []
        for c in client.containers.list():
            containers.append({
                'name': c.name,
                'image': c.image.tags[0] if c.image.tags else 'unknown',
                'status': c.status,
                'restart_count': c.attrs.get('RestartCount', 0),
            })
        return containers
    except Exception as e:
        logging.getLogger('docker').warning('Docker collection failed: %s', e)
        return []


def collect_software():
    """Collect software/OS info."""
    result = {
        'hostname': platform.node(),
        'os_distro': ' '.join(platform.dist()) if hasattr(platform, 'dist') else platform.platform(),
        'kernel': platform.release(),
    }
    try:
        import psutil
        result['uptime_s'] = int(psutil.boot_time())
    except Exception:
        pass
    # NVIDIA driver
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'],
                           capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            result['nvidia_driver'] = out.stdout.strip().split('\n')[0]
    except Exception:
        pass
    # Docker version
    try:
        out = subprocess.run(['docker', 'version', '--format', '{{.Server.Version}}'],
                           capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            result['docker_version'] = out.stdout.strip()
    except Exception:
        pass
    return result


def collect_errors():
    """Collect recent system errors."""
    errors = []
    try:
        out = subprocess.run(
            ['journalctl', '-p', 'err..crit', '--since', '5 min ago', '--no-pager', '-o', 'short-iso'],
            capture_output=True, text=True, timeout=10
        )
        seen = set()
        for line in out.stdout.strip().splitlines()[:20]:
            if line not in seen:
                seen.add(line)
                errors.append({
                    'source': 'kernel',
                    'message': line[:200],
                    'timestamp': line[:23] if len(line) > 23 else '',
                })
    except Exception as e:
        logging.getLogger('errors').warning('Error collection failed: %s', e)
    return errors


# ── Payload & Transport ─────────────────────────────────────────────────────

def build_payload(config):
    """Build the telemetry payload."""
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    inventory = {
        'cpu': collect_cpu(),
        'memory': collect_memory(),
        'motherboard': collect_motherboard(),
        'storage': collect_storage(),
        'network': collect_network(),
        'gpus': collect_gpus(),
    }

    metrics = {
        'cpu': collect_cpu(),
        'memory': collect_memory(),
        'storage': collect_storage(),
        'network': collect_network(),
        'gpus': collect_gpus(),
        'ai_processes': [],
        'docker_containers': collect_docker(),
    }

    payload = {
        'rig_uuid': config['rig_uuid'],
        'rig_name': config.get('rig_name', ''),
        'schema_version': __schema_version__,
        'agent_version': __version__,
        'timestamp': now,
        'inventory': inventory,
        'metrics': metrics,
        'software': collect_software(),
        'errors': collect_errors(),
    }

    return payload


def send_payload(config, payload):
    """Send payload to server with retry logic."""
    import time
    import random

    data = json.dumps(payload).encode('utf-8')
    # Django DRF does not auto-decompress gzip request bodies,
    # so we always send uncompressed JSON.
    use_gzip = False

    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': config['api_key'],
        'User-Agent': f'rig-monitor-agent/{__version__}',
    }

    max_retries = config.get('retry_attempts', 3)
    timeout = (3.0, 10.0)

    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"{config['server_endpoint']}/api/v1/ingest/",
                data=data,
                headers=headers,
                timeout=timeout,
            )
            logging.getLogger('transport').info(
                'Ingest response: %d %s', resp.status_code, resp.text[:100]
            )
            return resp.status_code, resp.json() if resp.content else {}
        except requests.exceptions.RequestException as e:
            logging.getLogger('transport').warning('Attempt %d failed: %s', attempt + 1, e)
            if attempt < max_retries - 1:
                delay = (2 ** attempt) + random.uniform(0, 0.4)
                time.sleep(delay)

    logging.getLogger('transport').error('All %d attempts failed', max_retries)
    return None, {}


# ── Main ────────────────────────────────────────────────────────────────────

def timeout_handler(signum, frame):
    raise TimeoutError("Collection exceeded time limit")


def main():
    config = load_config()
    setup_logging(debug=config.get('debug_mode', False))
    logger = logging.getLogger('main')

    # Hard timeout
    timeout_s = config.get('collection_timeout_s', 45)
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout_s)

    try:
        logger.info('Starting collection for rig %s', config['rig_uuid'])
        payload = build_payload(config)
        status_code, response = send_payload(config, payload)
        if status_code in (200, 202):
            logger.info('Payload accepted: %s', response.get('status', 'unknown'))
        else:
            logger.error('Payload rejected: %s %s', status_code, response)
    except TimeoutError:
        logger.error('Collection timed out after %ds', timeout_s)
        sys.exit(1)
    except Exception as e:
        logger.exception('Unexpected error: %s', e)
        sys.exit(1)
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
