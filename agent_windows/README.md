# GPU Rig Monitoring Agent — Windows

Windows-compatible agent for the GPU Rig Monitoring Platform. Collects hardware/software metrics and sends them to the monitoring server via HTTPS.

## Requirements

- Windows 10 or later
- Python 3.10+
- Network access to the monitoring server

## Quick Start

### 0. Create a Virtual Environment (Recommended)

Using a virtual environment keeps the agent's dependencies isolated from your system Python:

```powershell
cd agent_windows
python -m venv venv
.\venv\Scripts\Activate.ps1
```

> **Note:** If you get an execution policy error, run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` first, or use `.\venv\Scripts\activate.bat` in Command Prompt instead.

After activating the venv, your prompt should show `(venv)`. All `pip install` and `python run.py` commands below should be run inside the activated venv.

To deactivate when done:
```powershell
deactivate
```

### 1. Install Python Dependencies

Install core dependencies inside the activated venv:

```powershell
pip install psutil py-cpuinfo requests pyyaml wmi
```

Optional dependencies:

```powershell
pip install docker          # For Docker container monitoring
pip install nvidia-ml-py3   # For NVIDIA GPU monitoring (requires NVIDIA GPU with drivers)
```

### 1b. Freeze Dependencies (Optional)

To record exact versions for reproducibility:

```powershell
pip freeze > requirements.txt
```

To install from a requirements file on another machine:

```powershell
pip install -r requirements.txt
```

### 2. Configure

Copy the example config and edit it:

```powershell
copy config.yaml.example config.yaml
notepad config.yaml
```

Set these values:

```yaml
rig_uuid: "auto"
api_key: "your-api-key-from-dashboard"
server_endpoint: "https://monitor.example.com"
expected_gpu_count: 0
collection_timeout_s: 45
retry_attempts: 3
debug_mode: true
```

**To get an API key:** Log in to the monitoring dashboard → click **API Keys** → create a new key → copy it immediately (shown only once).

### 3. Test

```powershell
python run.py
```

You should see JSON log output. On success:

```json
{"ts":"2026-06-01T17:35:04","level":"INFO","module":"main","msg":"Starting collection for rig a1b2c3d4-..."}
{"ts":"2026-06-01T17:35:06","level":"INFO","module":"transport","msg":"Ingest response: 200 {\"status\": \"new\"}"}
{"ts":"2026-06-01T17:35:06","level":"INFO","module":"main","msg":"Payload accepted: new"}
```

### 4. Set Up Automatic Scheduling

> **Note:** The agent is designed to run every 60 seconds (matching the Linux cron schedule). Windows Task Scheduler's minimum interval is 1 minute. The `--install-task` flag configures 1-minute intervals, which is the closest practical equivalent. The built-in file lock prevents overlapping runs.

**Option A — Automatic (run as Administrator):**

```powershell
python run.py --install-task
```

This creates a Windows Task Scheduler entry that runs the agent every 1 minute.

Verify it was created:

```powershell
schtasks /query /tn GPURigMonitorAgent
```

**Option B — Manual via Task Scheduler GUI:**

```powershell
python run.py --help-task
```

Then follow the printed instructions.

**Option C — Manual via command line (run as Administrator):**

```powershell
schtasks /create /tn "GPURigMonitorAgent" /tr "python C:\path\to\agent_windows\run.py" /sc minute /mo 1 /f
```

### 5. Remove Scheduled Task

```powershell
python run.py --remove-task
```

Or:

```powershell
schtasks /delete /tn GPURigMonitorAgent /f
```

## Config Reference

| Field | Default | Description |
|-------|---------|-------------|
| `rig_uuid` | `"auto"` | Auto-generates a permanent UUID on first run. |
| `api_key` | — | **Required.** API key from the dashboard. |
| `server_endpoint` | — | **Required.** Server URL, no trailing slash. |
| `expected_gpu_count` | `0` | `0` = auto-detect. Set your GPU count to flag mismatches. |
| `collection_timeout_s` | `45` | Hard timeout for metric collection + upload. |
| `retry_attempts` | `3` | Retries on transient failures (exponential backoff). |
| `debug_mode` | `false` | `true` = verbose logging, no gzip compression. |

## File Layout

```
agent_windows/
├── run.py                   # Agent script
├── config.yaml.example      # Config template
├── config.yaml              # Your config (create from example)
└── logs/                    # Created automatically
    └── agent.log            # Structured JSON log (rotated at 10 MB)
```

## What Gets Collected

| Metric | Source | Windows | Linux |
|--------|--------|---------|-------|
| CPU model, cores, load, temp, utilization | psutil + cpuinfo + WMI | ✅ | ✅ |
| Memory (total, used, free, cached, swap) | psutil | ✅ | ✅ |
| Motherboard (manufacturer, model, BIOS) | WMI /sys/class/dmi | ✅ | ✅ |
| Disk (partitions, capacity, usage, SMART) | psutil + WMI / smartctl | ✅ | ✅ |
| Network (interfaces, bytes, errors, speed) | psutil + WMI /sysfs | ✅ | ✅ |
| GPU (NVIDIA: model, memory, util, temp, power) | pynvml | ✅* | ✅* |
| Docker containers | docker SDK | ✅† | ✅ |
| OS info (hostname, OS, kernel, uptime) | platform + psutil | ✅ | ✅ |
| NVIDIA driver version | nvidia-smi | ✅* | ✅* |
| System errors (last 5 min) | PowerShell Get-WinEvent / journalctl | ✅ | ✅ |

\* Requires NVIDIA GPU with drivers and `nvidia-ml-py3` installed.  
† Requires Docker Desktop running.

## Troubleshooting

### Agent won't start

```powershell
# Run with debug output
python run.py
# Check the logs directory
type logs\agent.log
```

### API key errors (401)

- Regenerate the key on the dashboard
- Update `config.yaml` with the new key
- Ensure there are no extra spaces or quotes

### Connection errors

```powershell
# Test connectivity to the server
curl https://monitor.example.com/api/v1/health/
```

### GPU metrics empty

Install NVIDIA support:

```powershell
pip install nvidia-ml-py3
```

Requires NVIDIA GPU with up-to-date drivers.

### SMART disk data unavailable

Ensure Task Scheduler is configured with **"Run with highest privileges"** — SMART queries require administrator access.

### Docker metrics empty

- Ensure Docker Desktop is running
- The docker SDK auto-detects the Windows named pipe for Docker Desktop

### High CPU during collection

The agent collects CPU metrics with a 1-second `psutil.cpu_percent(interval=1)` call. This is normal and only happens once per run.

## Differences from Linux Agent

| Feature | Linux (`agent/`) | Windows (`agent_windows/`) |
|---------|-----------------|--------------------------|
| **Scheduling** | cron (every 60s) | Windows Task Scheduler (every 1 min) |
| **Interval** | 60 seconds | 60 seconds (1 minute minimum via Task Scheduler) |
| **Lock file** | `flock` on `/var/lock/` | `msvcrt.locking` on `./logs/.agent.lock` |
| **Signal timeout** | `signal.SIGALRM` | Not needed (lock-based) |
| **Error collection** | `journalctl` | PowerShell `Get-WinEvent` |
| **System info** | `/sys/class/dmi/` | WMI (`Win32_BaseBoard`, `Win32_BIOS`) |
| **Disk SMART** | `sudo smartctl` | WMI `MSStorageDriver_FailurePredictStatus` |
| **Network speed** | `/sys/class/net/*/speed` | WMI `Win32_NetworkAdapter.Speed` |
| **Config path** | `/etc/monitoring-agent/config.yaml` | `./config.yaml` (alongside script) |
| **Log path** | `/var/log/monitoring-agent/` | `./logs/` (alongside script) |
| **Python deps** | `psutil py-cpuinfo requests pyyaml docker` | Same + `wmi` (+ `nvidia-ml-py3`, `docker` optional) |

## Command-Line Options

```
python run.py                 # Collect and send metrics
python run.py --install-task  # Create Windows Task Scheduler entry (Admin required)
python run.py --remove-task   # Remove Windows Task Scheduler entry (Admin required)
python run.py --help-task     # Print Task Scheduler setup instructions
```
