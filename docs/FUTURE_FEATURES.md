# GPU Rig Monitoring Platform — Top 10 Future Feature Suggestions (v2)

## Analysis Methodology
- Read full architecture documentation (1493 lines, v1.7)
- Analyzed all Django apps: accounts, audit, dashboard, metrics_app, rigs
- Identified gaps between current state and "Non-Goals (v1)" section
- Considered scalability path (§10.5), security boundaries (§7), and operational needs (§8)
- Prioritized by: user value, implementation complexity, architectural fit
- Excluded already-implemented features (Audit Log & Activity Feed, Log Rotation, Mobile UX)

---

## 1. 🔔 Alerting & Notifications System
**Priority:** HIGH | **Complexity:** MEDIUM | **New Django App:** `alerts`

**What:** Threshold-based alerts (email/Slack/Telegram) when metrics exceed limits — GPU temp > 85°C, disk usage > 90%, rig goes offline, etc.

**Why:** Currently listed as "Non-Goal (v1)" but is the #1 requested feature for any monitoring platform. The architecture already has all the data needed in LatestSnapshot.

**Architecture:**
- New `alerts` app with models: `AlertRule` (user FK, metric, threshold, condition, cooldown), `AlertHistory` (timestamp, value, message, channel)
- Celery beat task running every 60s checking LatestSnapshot against rules
- Notification channels: Email (existing SMTP), Slack webhook, Telegram bot
- Alert state machine: OK → PENDING (first trigger) → FIRING → ACKNOWLEDGED → OK
- Cooldown period to prevent alert storms
- Dashboard UI: Alert bell icon with count, alert history page, rule CRUD

**Edge cases:**
- Flapping alerts (metric oscillating around threshold) — use hysteresis (e.g., trigger at 85°C, clear at 80°C)
- Multiple users with different alert rules for same rig
- Alert acknowledgment and mute functionality
- Rate limiting for notifications (max 1 email per 5 min per rule)

---

## 2. 📊 Multi-Rig Comparison & Aggregation
**Priority:** HIGH | **Complexity:** MEDIUM | **Enhancement to:** `dashboard` + `metrics_app`

**What:** Compare metrics across multiple rigs on a single chart. Aggregate metrics across all rigs (fleet-wide averages, totals).

**Why:** Users with multiple rigs need to compare performance, identify underperforming units, and see fleet-wide trends.

**Architecture:**
- Extend `ChartDataView` to accept multiple `rig_uuid` parameters
- New "Fleet Charts" tab with aggregated views: average GPU temp across all rigs, total fleet power draw, fleet-wide error rate
- Rig selector UI (checkboxes or multi-select) on chart pages
- Comparison mode: overlay up to 5 rigs on same chart with different colors

**Edge cases:**
- Rigs with different GPU models — normalize or group by model
- Missing data for some rigs in the time range — show gaps, don't interpolate
- Large fleets (100+ rigs) — limit comparison to 5 rigs, use aggregation for fleet-wide

---

## 3. 🏷️ Rig Groups & Folders
**Priority:** HIGH | **Complexity:** LOW | **New Model in:** `rigs`

**What:** Organize rigs into named groups (e.g., "Farm A", "Test Rigs", "Production"). Filter fleet overview by group.

**Why:** As fleet grows beyond ~20 rigs, flat list becomes unmanageable. Users need logical grouping.

**Architecture:**
- New model: `RigGroup` (user FK, name, color, description)
- Add `group` FK to `Rig` model
- Fleet overview: group filter dropdown, collapsible group sections
- Group-level aggregation: average temp, total power, online count per group
- Drag-and-drop or multi-select to assign rigs to groups

**Edge cases:**
- Rig can belong to multiple groups (use M2M) or single group (use FK) — start with single group
- Default "Ungrouped" group for unassigned rigs
- Group-level alert rules (alert if any rig in group goes offline)

---

## 4. 🔄 Agent Auto-Update System
**Priority:** MEDIUM | **Complexity:** MEDIUM | **Enhancement to:** `agent/` + new server endpoint

**What:** Automatic agent version management — server tracks available versions, agents auto-update on schedule.

**Why:** Currently agents have `check_update.py` but it's not integrated with the server. Centralized version management is critical for fleet maintenance.

**Architecture:**
- New API endpoint: `GET /api/v1/agent/latest/` — returns latest version, download URL, changelog
- Agent periodically checks endpoint and downloads new version
- Server-side: `AgentVersion` model (version, release_date, download_url, changelog, is_stable)
- Admin UI: upload new agent version, set stable/beta channel, view update status per rig
- Rollout control: canary deployment (update 10% first), scheduled maintenance windows
- Rollback capability: agent keeps previous version as backup

**Edge cases:**
- Agent on different platforms (Linux/Windows) — separate version tracks
- Failed updates — agent reports update status, server shows failed rigs
- Version compatibility — server supports N-2 agent versions
- Bandwidth — agents stagger updates to avoid thundering herd

---

## 5. 📈 Custom Dashboards & Widget Layout
**Priority:** MEDIUM | **Complexity:** HIGH | **New Django App:** `dashboards`

**What:** Let users create custom dashboard pages with drag-and-drop widgets showing their most important metrics.

**Why:** Different users care about different metrics. GPU farmers want power/temp, ML engineers want GPU util, ops want uptime/errors.

**Architecture:**
- New `dashboards` app: `Dashboard` (user FK, name, layout JSON), `Widget` (dashboard FK, type, config JSON)
- Widget types: metric card, chart, rig list, alert summary, custom HTML
- Layout stored as JSON grid (rows × columns × widget references)
- Pre-built templates: "GPU Farmer", "ML Engineer", "Operations"
- Share dashboards between users (read-only)

**Edge cases:**
- Widget refresh intervals (some metrics update every 30s, others every 5 min)
- Mobile responsive layout (different grid for small screens)
- Performance: cache widget data, don't re-query on every poll

---

## 6. 🌐 Public Status Page
**Priority:** MEDIUM | **Complexity:** LOW | **New Django App:** `statuspage`

**What:** Public-facing status page showing fleet health without requiring login. Shareable URL.

**Why:** Users want to share their rig status with team members, clients, or on social media without giving full dashboard access.

**Architecture:**
- New `statuspage` app with `StatusPage` model (user FK, slug, is_public, show_metrics, show_rigs)
- Public URL: `/status/<slug>/` — no login required
- Shows: fleet summary (total rigs, online/offline), per-rig status (name, status, last seen), optional metrics
- Customization: logo, title, color scheme, which rigs to show
- Cache heavily (30s TTL) — no real-time updates needed
- Optional: embed code for external websites

**Edge cases:**
- Privacy — user controls which rigs/metrics are visible
- Rate limiting — prevent abuse of public endpoint
- Custom domain support (optional, advanced)

---

## 7. 📊 Reporting & Export
**Priority:** MEDIUM | **Complexity:** MEDIUM | **New Django App:** `reports`

**What:** Generate and schedule reports — daily/weekly/monthly summaries of fleet performance, uptime, power consumption.

**Why:** Users need reports for capacity planning, cost analysis, and stakeholder communication.

**Architecture:**
- `Report` model (user FK, name, type, schedule, format, recipients)
- Report types: fleet summary, per-rig performance, uptime SLA, power consumption, error summary
- Formats: PDF, CSV, HTML email
- Scheduling: Celery beat (daily at 9am, weekly Monday, monthly 1st)
- Email delivery using existing SMTP config
- On-demand report generation via "Export" button

**Edge cases:**
- Large fleets — generate reports asynchronously, notify when ready
- Data retention — reports reference historical data that may be compacted
- Custom date ranges — let users specify report period
- Template customization — company logo, custom headers

---

## 8. 🔌 Plugin System for Custom Metrics
**Priority:** LOW | **Complexity:** HIGH | **New Django App:** `plugins`

**What:** Allow users to write custom metric collectors (plugins) that run alongside the agent and report custom metrics.

**Why:** Advanced users want to monitor application-specific metrics — training loss, model throughput, custom hardware sensors.

**Architecture:**
- Plugin API: Python class with `collect()` method returning dict of metric_name → value
- Server-side: `Plugin` model (user FK, name, code, is_active, collection_interval)
- Agent downloads plugin code from server, executes in sandboxed subprocess
- Custom metrics stored in new `CustomMetric` model (rig_uuid, plugin FK, name, value, timestamp)
- Dashboard: custom metric cards and charts
- Plugin marketplace: share plugins with other users

**Edge cases:**
- Security — sandbox plugin execution (subprocess, resource limits, no network access)
- Error handling — plugin crashes shouldn't affect main agent
- Version compatibility — plugin API versioning
- Performance — plugins run in parallel, timeout after 10s

---

## 9. 👥 Multi-Tenancy & Team Management
**Priority:** LOW | **Complexity:** HIGH | **Enhancement to:** `accounts`

**What:** Support multiple teams/organizations with isolated data. Team admins can invite members, assign roles.

**Why:** Currently single-tenant. As platform grows, teams need isolation and role-based access.

**Architecture:**
- New `Organization` model (name, slug, plan, created_at)
- Add `organization` FK to all models (Rig, ApiKey, AlertRule, etc.)
- Roles: Owner, Admin, Member, Viewer
- Invitation system: email invite, accept/join
- Data isolation: all queries filtered by organization
- Billing integration (future): per-rig pricing, usage tracking

**Edge cases:**
- Data migration — existing single-tenant data needs organization assignment
- Cross-org features — some users belong to multiple orgs
- Performance — organization filter on every query (index required)
- Admin override — superusers can access all organizations

---

## 10. 🔐 Two-Factor Authentication (2FA)
**Priority:** MEDIUM | **Complexity:** LOW | **Enhancement to:** `accounts`

**What:** Add 2FA support (TOTP via authenticator apps) for user accounts.

**Why:** Security best practice, especially for admin/staff accounts. Protects against password compromise.

**Architecture:**
- Add `totp_secret` field to User model
- QR code setup page (generate TOTP secret, display QR code)
- Login flow: password → TOTP verification → session
- Backup codes for account recovery
- Optional per-user (not mandatory)
- Use `django-otp` or `pyotp` library

**Edge cases:**
- Lost authenticator — backup codes for recovery
- Staff accounts — can enforce 2FA for staff only
- API key auth — not affected (uses X-API-Key header, not session)
- Migration — existing users can enable 2FA voluntarily

---

## Summary Matrix

| # | Feature | Priority | Complexity | New App | Effort |
|---|---------|----------|------------|---------|--------|
| 1 | Alerting & Notifications | HIGH | MEDIUM | `alerts` | 2-3 days |
| 2 | Multi-Rig Comparison | HIGH | MEDIUM | enhancement | 1-2 days |
| 3 | Rig Groups & Folders | HIGH | LOW | `rigs` model | 0.5 day |
| 4 | Agent Auto-Update | MEDIUM | MEDIUM | enhancement | 1-2 days |
| 5 | Custom Dashboards | MEDIUM | HIGH | `dashboards` | 3-5 days |
| 6 | Public Status Page | MEDIUM | LOW | `statuspage` | 1 day |
| 7 | Reporting & Export | MEDIUM | MEDIUM | `reports` | 2-3 days |
| 8 | Plugin System | LOW | HIGH | `plugins` | 5-7 days |
| 9 | Multi-Tenancy | LOW | HIGH | `accounts` | 5-7 days |
| 10 | Two-Factor Authentication | MEDIUM | LOW | `accounts` | 1 day |

**Recommended implementation order:** 3 → 10 → 1 → 2 → 6 → 4 → 7 → 5 → 9 → 8
(Quick wins first, then high-value features, then complex infrastructure)

**Already implemented (from previous phase):**
- ✅ Audit Log & Activity Feed
- ✅ Log Rotation
- ✅ Mobile UX (Fleet Overview + Nav Bar)
- ✅ API Key Management (create, revoke, reactivate, delete, transfer)
- ✅ Chart Aggregation Fixes
- ✅ Name Collision Handling for Transfers
