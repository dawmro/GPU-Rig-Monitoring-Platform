# GPU Rig Monitoring Platform — Top 10 Future Feature Suggestions (v2)

## Analysis Methodology
- Read full architecture documentation (1496 lines, v1.7)
- Analyzed all Django apps: accounts, audit, dashboard, metrics_app, rigs
- Identified gaps between current state and "Non-Goals (v1)" section
- Considered scalability path (§10.5), security boundaries (§7), and operational needs (§8)
- Prioritized by: user value, implementation complexity, architectural fit
- Excluded already-implemented features (Audit Log & Activity Feed, Log Rotation, Mobile UX, Power Consumption, PCIe Monitoring)

---

## 1. 🔔 Alerting & Notifications System
**Priority:** HIGH | **Complexity:** MEDIUM | **New Django App:** `alerts`
**Status:** NOT STARTED

### Problem Statement
Currently the platform is "blind" — it collects and displays data, but never tells the user when something is wrong. A GPU running at 95°C for hours, a rig that went offline at 3 AM, a disk at 98% capacity — the user only discovers these by manually opening the dashboard. For a monitoring platform, this is the single largest gap between "useful" and "complete."

### Detailed Feature Description

**Threshold-based alerts** evaluate user-defined rules against incoming metric data (or against LatestSnapshot on a schedule). Each rule specifies:
- **Metric**: Which field to check (e.g., `gpu_temp_c`, `storage_usage_pct`, `rig.status`, `cpu_utilization_pct`, `mem_used_bytes`)
- **Condition**: `>`, `<`, `=`, `>=`, `<=`
- **Threshold**: The numeric trigger value (e.g., 85 for temperature, 90 for disk usage)
- **Scope**: Which rigs the rule applies to (specific rigs, all rigs, or rigs matching a tag)
- **Cooldown**: Minimum time between repeated alerts for the same trigger (e.g., 15 minutes)

**Rig status alerts** are a special case — instead of a numeric threshold, they fire on state transitions:
- `rig.status → offline` (went offline)
- `rig.status → online` (came back online)
- `rig.status → stale` (not reporting in for >2 minutes, still last-seen within 10 min)

**Notification channels:**
1. **Email** — using the existing SMTP configuration already in `settings.py`. One email per alert event, with metric value, timestamp, rig name, and a link to the dashboard.
2. **Slack** — incoming webhook URL per channel. Rich attachments with color-coded severity (green/yellow/red), metric details, and direct dashboard link.
3. **Telegram** — bot token + chat ID. Plain text message with metric info. Lightweight, works on mobile.

**Alert lifecycle state machine:**
```
OK ──(threshold exceeded)──→ PENDING (waiting for duration)
PENDING ──(still exceeding after N seconds)──→ FIRING
FIRING ──(metric drops below clear threshold)──→ OK
FIRING ──(user clicks "Acknowledge")──→ ACKNOWLEDGED
ACKNOWLEDGED ──(metric clears)──→ OK
```

**Anti-flapping hysteresis**: Instead of a single threshold, use two:
- Trigger threshold: 85°C (alert fires when exceeding this)
- Clear threshold: 80°C (alert clears only when dropping below this)
This prevents rapid toggle when temperature oscillates around 85°C (common with GPU fans cycling).

**Dashboard UI components:**
- **Bell icon** in the nav bar (with red badge count of unacknowledged firing alerts). Clicking it opens a dropdown showing last 5 alerts with severity colors.
- **Alert history page** (`/alerts/history/`) — paginated list with filtering by severity, date range, metric, rig. "Acknowledge All" button.
- **Rule management page** (`/alerts/rules/`) — CRUD for alert rules. Table view with columns: Metric, Condition, Threshold, Scope, Status (active/paused), Last Triggered. "Create Rule" button opens a form.
- **Pause/resume** individual rules (e.g., pause disk alerts during known heavy workloads).

### Data Model
```python
class AlertRule(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='alert_rules')
    name = models.CharField(max_length=100)  # e.g., "GPU Overheat"
    metric = models.CharField(max_length=50)  # field name in LatestSnapshot or payload
    condition = models.CharField(max_length=10)  # >, <, =, >=, <=
    threshold = models.FloatField()
    clear_threshold = models.FloatField(null=True)  # hysteresis (optional)
    duration_s = models.PositiveIntegerField(default=0)  # must exceed for N seconds before firing
    cooldown_s = models.PositiveIntegerField(default=900)  # 15 min between re-alerts
    scope = models.CharField(max_length=20, default='all')  # 'all', 'tag:<id>', 'rig:<uuid>'
    channels = models.JSONField(default=list)  # ['email', 'slack', 'telegram']
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AlertEvent(models.Model):
    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name='events')
    rig = models.ForeignKey(Rig, on_delete=models.CASCADE, related_name='alert_events')
    state = models.CharField(max_length=20)  # pending, firing, acknowledged, resolved
    metric_value = models.FloatField()  # the value that triggered it
    message = models.TextField()  # human-readable description
    triggered_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True)
    resolved_at = models.DateTimeField(null=True)
```

### Implementation Steps
1. Create `alerts` app with models (`AlertRule`, `AlertEvent`) + migration
2. Create management command `check_alerts.py` — queries LatestSnapshot/Rig against active rules, evaluates conditions, creates AlertEvent + sends notifications
3. Schedule via Celery beat (every 60s, matching agent interval)
4. Build views: rule CRUD, event history, bell dropdown
5. Add notification backends (email, Slack, Telegram) — pluggable adapter pattern
6. Wire into base template (bell icon in nav)

### Edge Cases
- **Flapping**: Hysteresis thresholds prevent oscillation. Duration requirement prevents single-spike triggers.
- **Same metric, different rules**: User can have both "GPU temp > 85°C (warning)" and "GPU temp > 95°C (critical)" — each fires independently.
- **Notification failure**: If email/Slack fails to send, log the error in errors.log and retry on next cycle. Don't lose the alert event.
- **Rig tags as scope**: Alert rules referencing tags must re-evaluate when rigs are tagged/untagged.
- **Time-based suppression**: Optional "quiet hours" (e.g., don't send email alerts 2 AM–7 AM unless critical severity).
- **Large fleets**: Celery task must handle pagination (check in batches of 50 rigs) to avoid memory issues at 1000+ rigs.

### Why This Is #1 Priority
Monitoring without alerting is "look at dashboard and remember to check often." Alerting makes the platform autonomous — it watches 24/7 and reaches out only when attention is needed. This is the difference between "tool" and "solution."

---

## 2. 📊 Multi-Rig Comparison & Aggregation
**Priority:** HIGH | **Complexity:** MEDIUM | **Enhancement to:** `dashboard` + `metrics_app`
**Status:** NOT STARTED

### Problem Statement
Current charts show data for **one rig at a time**. If a user has 4 rigs (e.g., rig-A, rig-B, rig-C, rig-D), they must open 4 separate tabs and mentally compare "rig-A averaged 72°C yesterday, rig-B averaged 68°C". Fleet owners need to answer questions like:
- Which rigs are running hotter than the fleet average?
- Is my new rig performing as well as my old ones?
- What's my total fleet power draw at peak hours?
- Which rig has the highest error rate?

These questions are impossible with single-rig charts alone.

### Detailed Feature Description

**Rig Selector UI**: A multi-select dropdown/checkbox list above the chart area. User selects 2–5 rigs. Chart updates to show all selected rigs as overlaid lines (each rig gets a distinct color from a predefined palette: blue, green, orange, red, purple).

**Comparison mode — Overlay Charts:**
- **Temperature overlay**: All selected rigs' GPU temperature lines on the same chart. Instantly visible which rigs run warmer.
- **Utilization overlay**: GPU utilization lines compared. Spot the underutilized or overworked rig.
- **Power overlay**: Power draw compared across rigs.
- **Custom Y-axis**: Each chart auto-scales to accommodate all rigs. If one rig maxes at 300W and another at 150W, both lines fit.

**Aggregation mode — Fleet Summary:**
Instead of overlaying individual rigs, show **fleet-wide aggregate** metrics:
- **Average GPU temp across all rigs** — single line showing fleet mean temperature over time.
- **Total fleet power draw** — sum of all rigs' power, showing total electrical load.
- **Fleet error rate** — sum of all error counts across rigs, showing if the fleet is healthy.
- **Fleet uptime percentage** — percentage of rigs currently online vs. total.

**Fleet Dashboard Widget** (new page or section on main dashboard):
- Summary cards at top: Total Rigs, Online/Offline Count, Avg GPU Temp, Total Power, Total Errors Today.
- Color-coded status distribution bar (e.g., green segment = online %, yellow = stale %, red = offline %).
- Table: "Top 5 Hottest Rigs" (sorted by avg temp, clickable to drill into detail).
- Table: "Rigs Below Average Utilization" (identify idle/underused hardware).

**Implementation approach:**
- Extend `/api/v1/rigs/<uuid>/chart-data/` to accept `?rig_uuids=uuid1,uuid2,uuid3` — returns datasets for each rig alongside each other for overlay.
- Add new endpoint `/api/v1/charts/fleet-summary/?metric=X&range=N` — returns aggregate (mean/sum) across all user's rigs.
- Frontend: Add rig multi-select UI, Call ChartDataView with multiple UUIDs for overlay, Add fleet summary cards widget to dashboard index page.

### Edge Cases
- **Different GPU models**: Rigs with RTX 3060 vs RTX 4090 have different temperature ranges. Chart label should include rig name + GPU model (e.g., "rig-A (RTX 3060)").
- **Missing data**: If a rig has no data in the selected time range, show a gap in the line chart (don't connect across gaps).
- **Large fleets (100+)**: Limit overlay comparison to 5 rigs (browser can't render more). For fleet-wide aggregation, use SQL-level aggregation (not loading all rows).
- **Mixed uptime**: Some rigs may have been online for the full period, others only part. Chart should reflect this honestly.

### Why This Matters
The platform's target users (GPU farmers, AI researchers, small data centers) almost always have multiple rigs. Comparing rigs is not a "nice to have" — it's how users discover hardware problems, plan upgrades, and optimize power allocation.

---

## 3. ⚡ Power Consumption & Cost Estimation
**Status:** ✅ IMPLEMENTED | **Priority:** — | **Deployed:** 2026-06

**What was implemented:**
- Agent collects GPU power draw (nvidia-smi via pynvml) and enforced power limit
- CPU power measured via RAPL sysfs (`/sys/class/powercap/intel-rapl:0/energy_uj`) or estimated from utilization × TDP (`8W × cores + 25W`)
- Other components (RAM+disks+MB+fans) = flat 50W
- PSU efficiency: 90% (user-configurable on User model)
- Total power calculation: `(gpu + cpu + 50) / 0.90 = total AC watts`
- `electricity_rate_kwh` on User model (default 0.33)
- `PowerReading` model stores historical power data (one row per minute, throttled)
- `LatestSnapshot` stores latest values: `power_total_w`, `power_gpu_w`, `power_cpu_w`, `power_other_w`
- **Live Metrics**: Power Consumption card with GPU/CPU/Other breakdown + cost/hr + est. daily
- **Charts**: GPU Power Draw (multi-GPU), CPU Power, Total System Power — all in Historical Charts tab
- **Fleet Overview**: Power [W] column (total system AC power, color-coded)

**Remaining (optional):**
- Power Breakdown stacked area chart (GPU/CPU/Other over time)
- Dedicated cost summary widget with weekly/monthly totals
- kWh trapezoidal integration for more accurate cost calculation

---

## 4. 🔄 Agent Auto-Update System
**Priority:** MEDIUM | **Complexity:** MEDIUM | **Enhancement to:** `agent/` + new server endpoint
**Status:** PARTIALLY BUILT — `check_update.py` exists for both Linux and Windows agents. Server-side integration (AgentVersion model, admin UI, rollout control) NOT STARTED.

### Problem Statement
Currently updating agents across a fleet requires:
1. SSH into each rig (or remote desktop for Windows)
2. Download the new `run.py`
3. Verify it works
4. Repeat for every rig

For a user with 10+ rigs, this is tedious and error-prone. And when a bug fix or new collector is released, the platform owner has no way to know which rigs are running outdated agents.

### What's Already Built
Both `agent/check_update.py` and `agent_windows/check_update.py` exist. They:
- Fetch the latest agent source from GitHub raw file
- Parse `__version__` from the remote file
- Compare with local `__version__`
- If newer: download, validate syntax, backup current, replace
- Log update events

### What's Missing
1. **Server-side `AgentVersion` model** — tracks available versions, release channels (stable/beta), changelogs
2. **Admin UI** — upload new agent version, view which rigs run which version, trigger rollouts
3. **Rollout control** — canary deployment (update 10% first, monitor for errors, then roll out to all)
4. **Update status reporting** — agents report their version on every heartbeat (already done via `agent_version` in payload), but server has no UI to display this
5. **Scheduled rollout** — update agents during maintenance windows, not immediately

### Detailed Feature Description

**Server-side version tracking:**
```python
class AgentVersion(models.Model):
    platform = models.CharField(max_length=10)  # 'linux' or 'windows'
    version = models.CharField(max_length=20)  # '1.5.15' or '1.6.15-win'
    release_date = models.DateTimeField()
    changelog = models.TextField()
    download_url = models.URLField()  # GitHub raw file URL
    is_stable = models.BooleanField(default=True)
    is_critical = models.BooleanField(default=False)  # force update for security fixes
```

**Admin UI:**
- **Version list** (`/admin/agent-version/`) — table of all uploaded versions with platform, date, stable/critical flags.
- **Upload new version** — admin uploads the agent file, sets changelog, marks stable/beta/critical.
- **Rig version dashboard** — shows a table of all rigs with their current agent version. Red highlight for outdated rigs. "Update Selected" button to trigger rollout.
- **Rollout progress** — during a rollout, show progress bar: "12/150 rigs updated (8%)".

**Rollout control:**
1. Admin selects target version + rollout strategy (all at once, or canary %).
2. Server marks the version as "rolling out" and records which rigs have been notified.
3. On next heartbeat, outdated rigs receive a response header `X-Agent-Update-Available: 1.5.15` with download URL.
4. Agent's `check_update.py` sees the header and performs the update.
5. After update, agent reports new version in next payload — server marks it as updated.

**Agent-side enhancement:**
- `check_update.py` checks both the GitHub raw file (existing) AND the server endpoint (`/api/v1/agent/latest/`) for version info.
- If server reports a newer version, agent downloads from server (not GitHub) — allows custom/private builds.
- Agent reports update status in payload: `agent_update_status: "current" | "failed:<error>" | "updated:<old_version>"`.

### Edge Cases
- **Failed updates**: Agent reports failure, server shows red in admin UI. Admin can retry or investigate.
- **Version compatibility**: Server supports N-2 agent versions. If agent is too old, server returns `426 Upgrade Required` with message.
- **Bandwidth**: Agents stagger updates (random 0–60 min delay) to avoid thundering herd hitting GitHub.
- **Windows file lock**: If agent is running when update is attempted, skip and retry next day.
- **Rollback**: Admin can mark a previous version as "current stable" — agents on newer versions get downgrade instruction.

### Why This Matters
Fleet management is the #1 operational burden for multi-rig deployments. Auto-update reduces "agent is running old version X" from a manual check to a dashboard glance. Critical for security patches (e.g., if a vulnerability is found in the agent's HTTP client).

---

## 5. 📈 Custom Dashboards & Widget Layout
**Priority:** MEDIUM | **Complexity:** HIGH | **New Django App:** `dashboards`
**Status:** NOT STARTED

### Problem Statement
The current dashboard is "one size fits all" — every user sees the same layout. But different users care about different things:
- **GPU farmer**: Wants power draw, temperature, cost per hour front and center
- **ML engineer**: Wants GPU utilization, VRAM usage, core clocks
- **Operations/DevOps**: Wants uptime, error counts, disk usage, network traffic
- **Researcher**: Wants custom metrics from their training jobs

Users currently have to scroll past irrelevant cards to find what they need.

### Detailed Feature Description

**Custom Dashboard Page:**
Each user can create multiple named dashboards (e.g., "Overview", "Power Monitoring", "GPU Health"). Each dashboard is a grid layout where users can add, remove, resize, and reposition widgets.

**Widget types:**
1. **Metric Card** — single large number with label and optional trend indicator (up/down arrow). Examples: "GPU Temp: 72°C ↑2°", "Total Power: 483W", "Uptime: 14d 6h".
2. **Line Chart** — time-series chart for any metric (GPU temp, power, utilization, etc.) with configurable time range (24h/7d/30d).
3. **Rig List** — mini table of rigs with configurable columns (name, status, temp, power). Filterable by tag.
4. **Alert Summary** — shows count of active/pending/firing alerts with bell icon.
5. **Fleet Summary** — aggregate stats (total rigs, online count, avg temp, total power).
6. **Custom HTML** — free-form HTML for power users (embed external gauges, custom notes).

**Layout system:**
- Grid-based: 12-column grid, widgets span 3–12 columns.
- Drag-and-drop: users drag widgets from a sidebar palette onto the grid.
- Responsive: on mobile (<768px), all widgets stack vertically regardless of grid config.
- Each widget has a config JSON: `{"type": "metric_card", "metric": "gpu_temp_c", "rig_scope": "all", "refresh_interval": 30}`.

**Pre-built templates:**
- "GPU Farmer": Power draw card, cost per hour card, GPU temp chart, fleet power total.
- "ML Engineer": GPU utilization chart, VRAM usage chart, core clock chart, GPU processes table.
- "Operations": Uptime card, error count card, disk usage chart, network traffic chart.
- "Minimal": Just rig status list + one key metric.

**Sharing:**
- Users can share a dashboard with another user (read-only view).
- Shared dashboards appear in a "Shared with Me" section.
- Owner can revoke sharing at any time.

### Data Model
```python
class Dashboard(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='dashboards')
    name = models.CharField(max_length=100)
    is_default = models.BooleanField(default=False)  # shown on home page
    created_at = models.DateTimeField(auto_now_add=True)

class Widget(models.Model):
    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name='widgets')
    widget_type = models.CharField(max_length=30)  # metric_card, chart, rig_list, etc.
    config = models.JSONField(default=dict)  # metric, scope, refresh, chart options
    position = models.PositiveSmallIntegerField()  # order in grid
    width = models.PositiveSmallIntegerField(default=4)  # 3-12 (12-column grid)
```

### Implementation Steps
1. Create `dashboards` app with models + migration
2. Build dashboard editor page (drag-and-drop grid, widget palette sidebar)
3. Build widget rendering engine (each widget type has a template + HTMX refresh)
4. Add pre-built templates (one-click apply)
5. Add sharing mechanism
6. Make "default" dashboard the home page for authenticated users

### Edge Cases
- **Widget refresh intervals**: Metric cards refresh every 30s (matching live metrics poll). Charts refresh on demand (user clicks ↻). Don't overload the server with 10 widgets all polling every 30s — stagger them.
- **Mobile responsive**: On <768px, all widgets stack at full width (12 columns). Grid config is ignored on mobile.
- **Performance**: Cache widget data for 30s. Don't re-query for every HTMX poll — use LatestSnapshot where possible.
- **Empty state**: If a user has no rigs, show a "Deploy your first rig" call-to-action instead of empty widgets.
- **Permission**: Users can only see their own rigs' data in widgets. Staff can see all.

### Why This Matters
Different roles in a team need different views. Custom dashboards let each team member build their own "command center" without forcing everyone into the same layout. This is the feature that transforms the platform from "monitoring tool" into "personal dashboard."

---

## 6. 🌐 Public Status Page
**Priority:** MEDIUM | **Complexity:** LOW | **New Django App:** `statuspage`
**Status:** NOT STARTED

### Problem Statement
Users want to share their fleet status with others without giving full dashboard access:
- Share with a client: "See, all 5 rigs are online and healthy"
- Share with a team member who doesn't have an account
- Embed in a company internal wiki or Slack sidebar
- Post on social media: "My 8-rig farm is running fine"

Currently the only way to share is to give someone your login credentials — which is a security risk and gives access to everything.

### Detailed Feature Description

**Public URL:** `https://yourserver.com/status/<random-slug>/` — no authentication required. The slug is a random 12-character string (e.g., `https://yourserver.com/status/a7k2m9x4pq3b/`). Only users who know the slug can view it.

**What it shows:**
- Fleet summary: total rigs, online count, offline count
- Per-rig status: name, status badge (Online/Stale/Offline), last seen
- Optional metrics: GPU temperature, power draw (if user enables it)
- Last updated timestamp (refreshes every 30s via HTMX polling)

**Customization options (set by owner):**
- **Page title**: "My GPU Farm Status" or company name
- **Show metrics**: toggle whether to display GPU temp/power on the public page
- **Show rig names**: toggle whether to display actual names or generic "Rig 1", "Rig 2"
- **Rig selection**: choose which rigs are visible (all, or specific ones)
- **Color scheme**: light/dark theme

**Security model:**
- The random slug IS the authentication — no login needed, but unguessable (12 chars from 62-char set = 62^12 ≈ 3.2 × 10^21 combinations).
- Owner can regenerate the slug at any time (invalidates old links).
- Owner can disable the public page entirely.
- No sensitive data exposed: no API keys, no email addresses, no historical charts, no error messages.
- Rate limited: 60 requests/minute per IP to prevent scraping.

**Embed code:** Optional HTML snippet users can paste into their own websites:
```html
<iframe src="https://yourserver.com/status/a7k2m9x4pq3b/embed/" 
        width="400" height="300" frameborder="0"></iframe>
```

### Data Model
```python
class StatusPage(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='status_page')
    slug = models.CharField(max_length=12, unique=True)  # random, URL-safe
    title = models.CharField(max_length=100, default="Fleet Status")
    is_active = models.BooleanField(default=True)
    show_metrics = models.BooleanField(default=False)
    show_rig_names = models.BooleanField(default=True)
    show_power = models.BooleanField(default=False)
    theme = models.CharField(max_length=10, default='dark')  # dark or light
    created_at = models.DateTimeField(auto_now_add=True)
```

### Implementation Steps
1. Create `statuspage` app with model + migration
2. Generate random slug on creation
3. Build public view (no auth) — reads from LatestSnapshot for the user's selected rigs
4. Build owner settings page (toggle options, regenerate slug, view URL/embed code)
5. Add HTMX polling for auto-refresh (30s)
6. Add rate limiting middleware
7. Add embed view (stripped-down version for iframe)

### Edge Cases
- **No rigs**: Show "No rigs configured" message instead of empty page.
- **All offline**: Show warning banner "All rigs are currently offline."
- **Slug collision**: Regenerate if collision occurs (extremely unlikely but handle it).
- **User deletes account**: Cascade delete StatusPage.
- **Abuse prevention**: Rate limit public endpoint. If slug is leaked, owner can regenerate.

### Why This Matters
This is a "shareability" feature. It doesn't add monitoring value — it adds **communication** value. Users who run GPU farms often need to prove uptime to clients, share status with investors, or just show off to friends. The public status page makes this trivial and secure.

---

## 7. 📊 Reporting & Export
**Priority:** MEDIUM | **Complexity:** MEDIUM | **New Django App:** `reports`
**Status:** NOT STARTED

### Problem Statement
Users need to answer questions that require looking at data across time periods and rigs:
- "What was my average GPU temperature last week?"
- "How much power did my fleet consume this month?"
- "Which rig had the most errors in the last 30 days?"
- "What's my fleet's uptime percentage for SLA reporting?"

Currently users can only answer these by manually looking at charts. Reports automate this.

### Detailed Feature Description

**Report types:**
1. **Fleet Summary** — overview of all rigs: count, online/offline, avg temp, total power, total errors.
2. **Per-Rig Performance** — detailed report for one rig: temp/power/utilization trends, peak values, averages.
3. **Uptime SLA** — percentage uptime per rig over a period. Shows total downtime duration.
4. **Power Consumption** — total kWh consumed, cost estimate, peak power, average power.
5. **Error Summary** — count of errors by source (kernel, nvidia, etc.) across all rigs.

**Output formats:**
- **PDF** — formatted report with header, charts, tables. Generated with WeasyPrint or ReportLab.
- **CSV** — raw data export for spreadsheet analysis.
- **HTML Email** — inline report sent directly to email (no download needed).

**Scheduling:**
- **Daily at 9 AM**: "Yesterday's Fleet Summary" — what happened in the last 24 hours.
- **Weekly Monday**: "Weekly Performance Report" — last 7 days aggregated.
- **Monthly 1st**: "Monthly Report" — last 30 days with cost estimates.
- **Custom**: User picks date range and generates on-demand.

**Report configuration:**
```python
class Report(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports')
    name = models.CharField(max_length=100)
    report_type = models.CharField(max_length=30)  # fleet_summary, per_rig, uptime, power, errors
    schedule = models.CharField(max_length=20)  # daily, weekly, monthly, manual
    format = models.CharField(max_length=10)  # pdf, csv, html
    recipients = models.JSONField(default=list)  # email addresses
    rigs = models.JSONField(default=list)  # list of rig_uuids, empty = all
    is_active = models.BooleanField(default=True)
```

**Implementation approach:**
1. Create `reports` app with model + migration
2. Build report generator functions (one per report type) — query timeseries tables with SQL aggregation.
3. Build report view (HTML preview for browser).
4. Build export views (PDF/CSV download).
5. Schedule with Celery beat (daily/weekly/monthly).
6. Email delivery: generate report → attach to email → send via existing SMTP.

### Edge Cases
- **Large fleets**: Reports for 100+ rigs over 30 days involve millions of rows. Use SQL aggregation (not Python loops). Consider capping at 50 rigs per report.
- **Data retention**: If data older than 31 days has been compacted, reports can only show hourly-granularity data for older periods. Report should note this.
- **No data**: If no rigs reported in the report period, show "No data available" instead of empty charts.
- **PDF generation timeout**: For large reports, generate asynchronously and notify via email when ready (don't make user wait).
- **Timezone**: Reports should use the user's timezone (currently server timezone). Consider adding a `timezone` field to User model.

### Why This Matters
Reports turn monitoring data into **business intelligence**. They're what managers, clients, and accountants actually want to see. Without reports, the platform is "engineers look at charts." With reports, it's "management gets automated briefings."

---

## 8. 🔌 Plugin System for Custom Metrics
**Priority:** LOW | **Complexity:** HIGH | **New Django App:** `plugins`
**Status:** NOT STARTED

### Problem Statement
Advanced users want to monitor metrics that the platform doesn't natively support:
- ML training loss, epoch time, model throughput
- Custom hardware sensors (temperature probes, fan controllers)
- Application-specific metrics (request queue depth, GPU memory fragmentation)
- Custom nvidia-smi fields not in the standard collector

The platform can't anticipate every metric users might want. A plugin system lets users extend the agent without modifying core code.

### Detailed Feature Description

**Plugin API:**
A plugin is a Python file with a `collect()` method:
```python
class MyPlugin:
    name = "ml_training"  # unique identifier
    interval = 60  # seconds between collections
    
    def collect(self, context):
        """Return dict of metric_name → value."""
        return {
            "training_loss": get_current_loss(),
            "epoch_time_s": get_epoch_time(),
            "tokens_per_second": get_tps(),
        }
```

**Server-side:**
- `Plugin` model: user FK, name, code (Python source), is_active, collection_interval
- Agent downloads active plugins from server on startup
- Agent executes plugins in a sandboxed subprocess (timeout 10s, no network, limited memory)
- Results are included in the payload under `custom_metrics: {plugin_name: {metric: value}}`
- `CustomMetric` model stores historical data (rig_uuid, plugin_name, name, value, timestamp)
- Dashboard shows custom metric cards and charts

**Plugin marketplace:**
- Users can share plugins (publish to a shared catalog)
- Other users can install shared plugins with one click
- Version tracking: plugin version + compatibility range

### Data Model
```python
class Plugin(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='plugins')
    name = models.CharField(max_length=100)  # unique per user
    code = models.TextField()  # Python source
    is_active = models.BooleanField(default=True)
    collection_interval = models.PositiveIntegerField(default=60)
    is_public = models.BooleanField(default=False)  # share with other users

class CustomMetric(models.Model):
    rig_uuid = models.UUIDField(db_index=True)
    plugin = models.ForeignKey(Plugin, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    value = models.FloatField()
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
```

### Implementation Steps
1. Create `plugins` app with models + migration
2. Add `custom_metrics` section to IngestSerializer and payload processing
3. Build plugin execution engine in agent (subprocess sandbox)
4. Build plugin management UI (create, edit, activate/deactivate, share)
5. Build custom metric display on dashboard (cards + charts)
6. Add marketplace UI (browse public plugins, install)

### Edge Cases
- **Security**: Plugins run with the agent's permissions. A malicious plugin could:
  - Read files from disk (API keys, passwords)
  - Make network requests (DDoS, exfiltrate data)
  - Consume CPU/memory (crash the agent)
  - Mitigation: Execute in subprocess with resource limits (10s timeout, 256MB memory), no network access (iptables/nftables drop outbound from agent process), read-only filesystem.
- **Plugin crashes**: If a plugin raises an exception, log the error and continue. Don't crash the main agent. Mark plugin as "error" in dashboard.
- **Version compatibility**: If the plugin API changes (e.g., new context field), old plugins should still work. Use semantic versioning for the plugin API.
- **Performance**: Plugins run in parallel (thread pool, max 4 concurrent). Total plugin execution must not exceed agent's 30s collection timeout.

### Why This Is Low Priority
The plugin system is powerful but complex to build safely. Most users' needs are already covered by the built-in collectors (GPU, CPU, memory, storage, network, Docker). Plugins are for edge cases — users with unusual hardware or custom software. It's better to focus on the higher-value features (alerting, comparison) first and add plugins later when the platform is stable.

---

## 9. 👥 Multi-Tenancy & Team Management
**Priority:** LOW | **Complexity:** HIGH | **Enhancement to:** `accounts`
**Status:** NOT STARTED

### Problem Statement
Currently the platform is single-tenant — one database, one user set, one organization. This works for individual users but breaks down when:
- A company has multiple departments that should not see each other's rigs
- A hosting provider wants to offer the platform as SaaS to multiple clients
- A user wants to give a contractor access to only specific rigs

### Detailed Feature Description

**Organization model:**
```python
class Organization(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    plan = models.CharField(max_length=20, default='free')  # free, pro, enterprise
    created_at = models.DateTimeField(auto_now_add=True)
```

**Roles and permissions:**
- **Owner**: Full admin (manage billing, delete org, manage members)
- **Admin**: Manage rigs, members, alerts within the org
- **Member**: View assigned rigs, view dashboards
- **Viewer**: Read-only access to all org rigs

**Data isolation:**
All models get an `organization` FK:
- `Rig.organization`, `ApiKey.organization`, `AlertRule.organization`, etc.
- All queries filtered by `request.user.organization`
- Database-level isolation via Django's queryset filtering (or PostgreSQL RLS for stricter isolation)

**Invitation system:**
- Admin enters email address → system sends invite link
- Link contains signed token (expires in 7 days)
- Recipient creates account (or logs in if exists) and joins org
- Invitation can be revoked by admin

### Implementation Steps
1. Add `Organization` model + `organization` FK to all models
2. Create data migration to assign existing data to a "default" organization
3. Add middleware to set `request.organization` from user's org
4. Update all views to filter by organization
5. Add invitation system (model, views, email templates)
6. Add role-based access control (decorators, mixins)
7. Add org management UI (member list, invite, remove, change roles)

### Edge Cases
- **Existing data**: All existing rigs/users need to be assigned to an organization. Create a "Personal" org for single-tenant users.
- **Cross-org features**: Staff (superusers) can see all organizations. Need a special `is_org_staff` flag.
- **Performance**: Adding `organization` FK to every query requires indexes on all org FK fields.
- **API keys**: Currently scoped to user. With orgs, API keys should be scoped to org (or inherit org from user).

### Why This Is Low Priority
Multi-tenancy is infrastructure-heavy (touches every model and every view) and the current target users (individual GPU farmers, small teams) don't need it. It becomes relevant only when the platform is offered as SaaS or deployed in enterprise environments. The effort (5-7 days) is better spent on higher-value features first.

---

## 10. 🔐 Two-Factor Authentication (2FA)
**Priority:** MEDIUM | **Complexity:** LOW | **Enhancement to:** `accounts`
**Status:** NOT STARTED

### Problem Statement
Currently accounts are protected only by passwords. If a password is compromised (phishing, credential stuffing, weak password), an attacker gains full access to the dashboard — can see all rigs, manage API keys, rename/delete rigs, and transfer ownership.

### Detailed Feature Description

**TOTP-based 2FA:**
- User scans a QR code with an authenticator app (Google Authenticator, Authy, 1Password, etc.)
- During login, after entering password, user enters the 6-digit code from their app
- TOTP codes change every 30 seconds

**Setup flow:**
1. User goes to Profile → Security → "Enable 2FA"
2. Server generates a TOTP secret, displays QR code
3. User scans QR code with authenticator app
4. User enters a verification code to confirm setup
5. Server generates 10 backup codes (one-time use) for account recovery

**Login flow:**
1. User enters email + password → server validates
2. If 2FA enabled, server prompts for TOTP code
3. User enters 6-digit code → server validates
4. On success, session is created

**Recovery flow:**
- If user loses their authenticator (phone broken/lost), they can use one of the 10 backup codes
- If all backup codes are used, user contacts admin for manual reset
- Admin can disable 2FA for any user from Django admin

**Configuration:**
```python
class User(AbstractUser):
    totp_secret = models.CharField(max_length=32, blank=True)  # base32 encoded
    is_2fa_enabled = models.BooleanField(default=False)
```

**Optional vs. mandatory:**
- By default, 2FA is optional (users can enable voluntarily)
- Staff/admin accounts can be forced to have 2FA (configurable via `STAFF_REQUIRE_2FA=True`)
- API key authentication is NOT affected (uses X-API-Key header, bypasses 2FA)

### Implementation Steps
1. Add `totp_secret` and `is_2fa_enabled` fields to User model
2. Install `pyotp` library (pure Python, no external dependencies)
3. Build setup page (QR code display, verification)
4. Build login flow modification (password → TOTP prompt)
5. Build backup code generation and recovery flow
6. Add admin controls (force 2FA for staff, disable 2FA for user)

### Edge Cases
- **Lost authenticator**: Backup codes allow recovery. If all codes used, admin intervention required.
- **Time sync**: TOTP relies on server time. If server clock drifts, codes fail. Use NTP (already required for agent-server communication).
- **Existing sessions**: When 2FA is enabled, existing sessions are invalidated (user must re-authenticate with 2FA).
- **API keys**: Not affected. API key auth is separate from session auth and doesn't go through 2FA.
- **Migration**: Existing users can enable 2FA voluntarily. No forced migration.

### Why This Matters
Security is a "you only need it when you need it" feature. A compromised account in a multi-rig setup means the attacker can:
- See all rig names, IP addresses, and locations
- Revoke API keys (disrupting monitoring)
- Delete rigs and their historical data
- Transfer rigs to their own account

2FA makes all of these attacks significantly harder. It's low complexity (1 day) and high impact.

---

## Summary Matrix

|| # | Feature | Priority | Complexity | New App | Effort |
||---|---------|----------|------------|---------|--------|
|| 1 | Alerting & Notifications | HIGH | MEDIUM | `alerts` | 2-3 days |
|| 2 | Multi-Rig Comparison | HIGH | MEDIUM | enhancement | 1-2 days |
|| 3 | Power Consumption & Cost Tracking | ✅ IMPLEMENTED | — | — | — |
|| 4 | Agent Auto-Update | MEDIUM | MEDIUM | enhancement | 1-2 days |
|| 5 | Custom Dashboards | MEDIUM | HIGH | `dashboards` | 3-5 days |
|| 6 | Public Status Page | MEDIUM | LOW | `statuspage` | 1 day |
|| 7 | Reporting & Export | MEDIUM | MEDIUM | `reports` | 2-3 days |
|| 8 | Plugin System | LOW | HIGH | `plugins` | 5-7 days |
|| 9 | Multi-Tenancy | LOW | HIGH | `accounts` | 5-7 days |
|| 10 | Two-Factor Authentication | MEDIUM | LOW | `accounts` | 1 day |

**Recommended implementation order:** 10 → 1 → 2 → 6 → 4 → 7 → 5 → 9 → 8
(Quick wins first, then high-value features, then complex infrastructure)

**Already implemented (from previous phase):**
- ✅ Audit Log & Activity Feed
- ✅ Log Rotation
- ✅ Mobile UX (Fleet Overview + Nav Bar)
- ✅ API Key Management (create, revoke, reactivate, delete, transfer)
- ✅ Chart Aggregation Fixes
- ✅ Name Collision Handling for Transfers
- ✅ Tag-based rig grouping (covers Rig Groups & Folders use case)
- ✅ Power Consumption Tracking (agent collects power draw, user electricity rate, dashboard display)
- ✅ PCIe Link Monitoring (agent collects, server stores, dashboard displays)
