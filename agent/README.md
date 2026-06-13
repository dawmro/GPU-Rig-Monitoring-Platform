# GPU Rig Monitoring Agent — Linux

**Version:** 1.4.0-linux | **Schema:** 1.2

Linux agent for the GPU Rig Monitoring Platform. Collects hardware/software metrics via `psutil`, `pynvml`, and system interfaces, then POSTs them to the monitoring server every 60 seconds via cron.

## Requirements

- Linux (Ubuntu 20.04+, Debian 11+, or similar)
- Python 3.10+
- Network access to the monitoring server
- Root/sudo access for installation

## Quick Start

### 1. Transfer Agent Files to the Rig

```bash
# From your local machine or the server:
rsync -avz /path/to/agent/ root@RIG_IP:/tmp/agent/
```

### 2. Run the Installer

```bash
ssh root@RIG_IP
chmod +x /tmp/agent/install.sh
/tmp/agent/install.sh
```

The installer performs these operations:

| Step | What It Does |
|------|-------------|
| 1 | Creates `monitoring-agent` system user (no-login shell) |
| 2 | Creates directories: `/opt/monitoring-agent/`, `/etc/monitoring-agent/`, `/var/log/monitoring-agent/` |
| 3 | Creates Python virtualenv and installs dependencies (`psutil`, `py-cpuinfo`, `requests`, `pyyaml`, `docker`, `nvidia-ml-py3`) |
| 4 | Copies `run.py` and creates config template at `/etc/monitoring-agent/config.yaml` |
| 5 | Configures sudoers for SMART disk queries, NVMe logs, and journalctl (read-only) |
| 6 | Creates cron job — runs every 60 seconds with `flock` to prevent overlaps |
| 7 | Schedules daily auto-update check (random time to avoid thundering herd) |

### 3. Configure

Edit the config file on the rig:

```bash
nano /etc/monitoring-agent/config.yaml
```

Set these values:

```yaml
rig_uuid: "auto"
rig_name: "gpu-server-01"
api_key: "your-api-key-from-dashboard"
server_endpoint: "https://monitor.example.com"
expected_gpu_count: 0
collection_timeout_s: 45
retry_attempts: 3
debug_mode: false
```

### 4. Test

```bash
sudo -u monitoring-agent /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/run.py
```

Expected output:

```json
{"ts":"2026-06-01T17:35:04","level":"INFO","module":"main","msg":"Starting collection for rig a1b2c3d4-..."}
{"ts":"2026-06-01T17:35:06","level":"INFO","module":"transport","msg":"Ingest response: 200 {\"status\": \"new\"}"}
{"ts":"2026-06-01T17:35:06","level":"INFO","module":"main","msg":"Payload accepted: new"}
```

The cron job will start automatically within 1 minute.

## Config Reference

| Field | Default | Description |
|-------|---------|-------------|
| `rig_uuid` | `"auto"` | Auto-generates a permanent UUID on first run. |
| `rig_name` | `""` | Suggested initial name for this rig. Used ONLY once during first registration. Leave empty to use hostname. After creation, rename via the dashboard. |
| `api_key` | — | **Required.** API key from the dashboard. |
| `server_endpoint` | — | **Required.** Server HTTPS URL, no trailing slash. |
| `expected_gpu_count` | `0` | `0` = auto-detect. Set your GPU count to flag mismatches. |
| `collection_timeout_s` | `45` | Hard timeout for metric collection + upload. |
| `retry_attempts` | `3` | Retries on transient failures (exponential backoff). |
| `debug_mode` | `false` | `true` = verbose logging, no gzip compression. |

**To get an API key:** Log in to the monitoring dashboard → click **API Keys** → create a new key → copy it immediately (shown only once).

## File Layout

```
/opt/monitoring-agent/
├── run.py                   # Agent script
├── venv/                    # Python virtual environment
└── check_update.py          # Auto-update checker

/etc/monitoring-agent/
└── config.yaml              # Agent config (mode 0600)

/var/log/monitoring-agent/
├── agent.log                # Structured JSON log (rotated at 10 MB × 3 backups)
├── payload.json             # Latest payload sent to server (overwritten each run)
├── cron.log                 # Cron output log
└── update.log               # Auto-update check log

/etc/cron.d/
├── monitoring-agent         # Agent schedule (every 60s)
└── monitoring-agent-update  # Auto-update check (daily at random time)
```

## What Gets Collected

| Metric | Source | Linux | Windows |
|--------|--------|-------|---------|
| CPU model, cores, load, temp, utilization | psutil + cpuinfo + sysfs | ✅ | ✅ |
| Memory (total, used, free, cached, swap) | psutil | ✅ | ✅ |
| Motherboard (manufacturer, model, BIOS) | `/sys/class/dmi/` | ✅ | ✅ |
| Storage (partitions, capacity, usage, SMART/NVMe) | psutil + `smartctl`/`nvme` | ✅ | ✅ |
| Network (interfaces, bytes, errors, speed) | psutil + sysfs | ✅ | ✅ |
| GPU (model, memory, util, temp, power, fan) | `pynvml` | ✅* | ✅* |
| Docker containers | docker SDK | ✅† | ✅† |
| OS info (hostname, OS, kernel, uptime) | `/etc/os-release` + psutil | ✅ | ✅ |
| NVIDIA driver version | `nvidia-smi` | ✅* | ✅* |
| System errors (last 5 min) | `journalctl` | ✅ | ✅ |
| PCIe link speed/width | `nvidia-smi -q` | ✅* | ✅* |
| Process-level GPU memory | `pynvml` | ✅* | ✅* |

\* Requires NVIDIA GPU with drivers and `nvidia-ml-py3` installed.
† Requires Docker daemon running.

## Agent Permissions

The `monitoring-agent` system user runs without root but needs elevated access for specific hardware queries:

| Command | Purpose |Risk |
|---------|---------|------|
| `/usr/sbin/smartctl` | Read disk SMART health data (SATA) | Read-only |
| `/usr/sbin/nvme` | Read NVMe drive health/temperature | Read-only |
| `/bin/journalctl` | Read system error logs | Read-only |

These are granted via `/etc/sudoers.d/monitoring-agent`:

```
Defaults:monitoring-agent !authenticate
monitoring-agent ALL=(root) NOPASSWD: /usr/sbin/smartctl, /usr/bin/smartctl, /bin/journalctl, /usr/bin/journalctl, /usr/sbin/nvme, /usr/bin/nvme
```

**Critical:** The `Defaults:monitoring-agent !authenticate` line is **required** for system users with `nologin` shell. Without it, PAM authentication fails. The `!authenticate` default tells sudo to skip PAM entirely; `NOPASSWD` alone is insufficient.

**GPU monitoring** does NOT require root — `pynvml` reads from the NVIDIA driver interface, accessible to all users.

## Auto-Update

The installer schedules a daily auto-update check at a random time. The update mechanism:

1. Checks GitHub for a newer agent version
2. Downloads the new `run.py`
3. Backs up the current version
4. Validates the new version syntax
5. Atomically replaces the running script

Manual check:
```bash
sudo /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/check_update.py
```

Force reinstall:
```bash
sudo /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/check_update.py --force
```

Logs: `/var/log/monitoring-agent/update.log`

## Differences from Windows Agent

| Feature | Linux (`agent/`) | Windows (`agent_windows/`) |
|---------|-----------------|--------------------------|
| **Scheduling** | cron (every 60s) | Windows Task Scheduler (every 1 min) |
| **Lock file** | `flock` on `/var/lock/` | `msvcrt.locking` on `./logs/.agent.lock` |
| **Signal timeout** | `signal.SIGALRM` | Not needed (lock-based) |
| **Error collection** | `journalctl` | PowerShell `Get-WinEvent` |
| **System info** | `/sys/class/dmi/` | WMI (`Win32_BaseBoard`, `Win32_BIOS`) |
| **Disk SMART** | `sudo smartctl` / `nvme` | WMI `MSStorageDriver_FailurePredictStatus` |
| **Network speed** | `/sys/class/net/*/speed` | WMI `Win32_NetworkAdapter.Speed` |
| **Config path** | `/etc/monitoring-agent/config.yaml` | `./config.yaml` (alongside script) |
| **Log path** | `/var/log/monitoring-agent/` | `./logs/` (alongside script) |
| **Python deps** | `psutil py-cpuinfo requests pyyaml docker` | Same + `wmi` (+ `pynvml`, `docker` optional) |
| **Installation** | `install.sh` (bash) | `--install-task` flag or manual Task Scheduler |
| **Auto-update** | cron-scheduled `check_update.py` | Built into `run.py` via CLI flag |

## Troubleshooting

### Agent won't start

```bash
# Run with debug output
sudo -u monitoring-agent /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/run.py
# Check logs
tail -50 /var/log/monitoring-agent/agent.log
```

### API key errors (401)

- Regenerate the key on the dashboard
- Update `/etc/monitoring-agent/config.yaml` with the new key
- Ensure no extra spaces or quotes

### Connection errors

```bash
# Test connectivity to the server
curl -v https://monitor.example.com/api/v1/health/
```

### GPU metrics empty

```bash
# Install NVIDIA support
sudo /opt/monitoring-agent/venv/bin/pip install nvidia-ml-py3
```

Requires NVIDIA GPU with up-to-date drivers. If you see `FutureWarning: The pynvml package is deprecated`, install `nvidia-ml-py` instead — both provide the `pynvml` module.

### SMART/NVMe disk data unavailable

```bash
# Install disk tools
sudo apt install smartmontools nvme-cli
# Verify sudoers config
sudo -l -U monitoring-agent
```

### Cron job not running

```bash
# Check cron is running
systemctl status cron
# Check cron log
tail -50 /var/log/monitoring-agent/cron.log
# Verify cron file exists
cat /etc/cron.d/monitoring-agent
```

### Stale lock file (agent hangs/overlaps)

```bash
rm -f /var/lock/monitoring-agent.lock
```

### Permission denied on config

```bash
sudo chown monitoring-agent:monitoring-agent /etc/monitoring-agent/config.yaml
sudo chmod 600 /etc/monitoring-agent/config.yaml
```

## Command-Line Options

```
python3 run.py                 # Collect and send metrics
python3 run.py --dry-run       # Print payload to stdout without sending
python3 run.py --debug         # Enable verbose logging
```
