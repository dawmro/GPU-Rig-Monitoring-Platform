# Fleet Overview — "All 12+ Columns at a Glance" Design

> **Status:** Design plan (not yet implemented) — branch `feat/ui-ux-research`
> **Date:** 2026-09-06
> **Author:** UI/UX research
> **Related docs:** `docs/GPU_Rig_Monitoring_Architecture.md`, README

---

## 🎯 Core Design Constraint

> **"It is important for the user to gain the most info about the status of their rigs
> just from looking at the Fleet Overview tab alone. Therefore many parameters read
> fast from latest snapshot and color coding for values."**

This is a **non-negotiable design constraint** that overrides conventional
"progressive disclosure" UI patterns. Every metric must be visible without
hovering, clicking, or navigating. Color coding is the primary visual
language — it must be **consistent** (same metric = same color everywhere) and
**dense** (multi-GPU/multi-disk rigs must show all values).

### **Why "12+ columns" and not "6 columns + drill-down"**

- The user has **rigs that may be in another building/room** — they need to spot
  problems at a glance without clicking each rig.
- Operators want to **see patterns across rigs** (e.g., "all 4 of my 3060 rigs
  are at 95% GPU temp — check cooling") — this requires ALL parameters to be
  visible for cross-rig comparison.
- When something goes wrong at 3 AM, **scanning 100 rigs in 1 second** is more
  important than a clean minimalist design.

---

## 📊 Available Data (Already in `LatestSnapshot`)

Confirmed from DB inspection — all of this is read with **0 time-series queries**:

### **Per-rig scalars** (1 value per rig)
| Field | Type | Example | Use |
|-------|------|---------|-----|
| `cpu_utilization_pct` | float | 22.5 | CPU column |
| `cpu_temp_c` | float | 65.0 | CPU Temp column |
| `cpu_freq_current_mhz` | float | 3001.0 | CPU Freq column |
| `cpu_load_avg_json` | list[3] | [3.6, 3.6, 3.6] | CPU Load column |
| `mem_used_bytes` / `mem_total_bytes` | int / int | 34GB / 64GB | Mem column |
| `power_total_w` / `power_cpu_w` / `power_gpu_w` | float | 154.8 | Power column |
| `process_count` | int | 247 | Processes column |
| `gpu_count` | int | 1-8 | GPU model column |
| `gpu_process_count` | int | 15 | GPU processes column |
| `has_active_job` | bool | true/false | Job indicator |
| `agent_version` | str | 1.9.1 | Agent column |

### **Per-rig JSON arrays** (1 value per GPU/disk/iface)
| Field | Example | Use |
|-------|---------|-----|
| `gpu_models_json` | ["RTX 3060"] | GPU model column |
| `gpu_temps_json` | [55] | GPU Temp column (per-GPU) |
| `gpu_utils_json` | [88] | GPU Util column (per-GPU) |
| `gpu_fans_json` | [0] | GPU Fan column (per-GPU) |
| `gpu_power_draws_json` | [62.2] | GPU Power column (per-GPU) |
| `gpu_mem_util_pcts_json` | [67.6] | GPU VRAM column (per-GPU) |
| `storage_usage_pcts_json` | [55.8, 73.5, 75.7, 72.3, 0.0] | Disk column (per-disk) |
| `storage_temps_json` | [None, None, ...] | Disk Temp column (per-disk) |
| `network_rx_bytes_json` | [356116137218, 9, 291] | Network column (per-iface) |
| `network_tx_bytes_json` | [22855007472, 834, 1592] | Network column (per-iface) |

**All of this is read with 0 time-series queries** — the previous
`fix/chart-data-view-perf` branch already optimized this.

---

## 🎨 Color System (Single Source of Truth)

**5-tier semantic colors** matching the user's existing pattern
(`cpu_util_color` filter):

| Tier | % Range | DaisyUI Color | Tailwind fallback | Use |
|------|---------|---------------|-------------------|-----|
| **Critical** | > 80% | `text-error` `bg-error/20` | `text-red-400` | Action required |
| **Warning** | 60-80% | `text-warning` `bg-warning/20` | `text-orange-400` | Approaching limit |
| **Caution** | 40-60% | `text-info` `bg-info/20` | `text-yellow-400` | Watch |
| **Normal** | 20-40% | `text-success` `bg-success/20` | `text-green-400` | Healthy |
| **Idle/None** | ≤ 20% or null | `text-base-content/40` | `text-gray-500` | Idle / no data |

**Inverted for GPU Util (high = good):**

| Tier | % Range | Color | Use |
|------|---------|-------|-----|
| **Throttling** | > 95% | `text-error` `bg-error/20` | GPU throttling |
| **Maxed** | 80-95% | `text-warning` `bg-warning/20` | Hot |
| **Working** | 50-80% | `text-success` `bg-success/20` | Active |
| **Light** | 20-50% | `text-info` `bg-info/20` | Light load |
| **Idle** | ≤ 20% | `text-base-content/40` | Idle |

**For temperature (°C, GPU and CPU):**

| Tier | Range | Color | Use |
|------|-------|-------|-----|
| **Hot** | > 85°C | `text-error` `bg-error/20` | Throttling imminent |
| **Warm** | 70-85°C | `text-warning` `bg-warning/20` | Hot |
| **Normal** | 50-70°C | `text-success` `bg-success/20` | OK |
| **Cool** | < 50°C | `text-info` `bg-info/20` | Cool |
| **No data** | None | `text-base-content/40` | N/A |

**For fan speed (%):**

| Tier | Range | Color | Use |
|------|-------|-------|-----|
| **Maxed** | > 90% | `text-error` `bg-error/20` | Fan maxed, possible issue |
| **High** | 60-90% | `text-warning` `bg-warning/20` | High RPM |
| **Normal** | 30-60% | `text-success` `bg-success/20` | Normal |
| **Idle** | < 30% | `text-info` `bg-info/20` | Low RPM |
| **No data** | None | `text-base-content/40` | N/A |

**For VRAM/Memory (%, 0-100):** Same as util (>80 = error, etc.)

**For network errors:** 0 = success, > 0 = error

---

## 🧩 The 3 "Color" Components (Reusable)

### **1. `metric_value` filter (single value → color class)**

Replaces `cpu_util_color` and the 10+ inline conditionals. Single source of truth.

```python
# gpu_monitor/dashboard/templatetags/metric_colors.py
from django import template
register = template.Library()

@register.filter
def metric_color(value, metric_type='util'):
    """Return DaisyUI semantic color class for a metric value.

    Args:
        value: The numeric value (or None)
        metric_type: One of 'util', 'util_inv' (inverted, e.g., GPU util),
                     'temp' (°C), 'fan' (%), 'error' (count, > 0 = bad)
    Returns:
        DaisyUI semantic class: text-error, text-warning, text-info, text-success,
        or text-base-content/40
    """
    if value is None:
        return 'text-base-content/40'
    try:
        v = float(value)
    except (ValueError, TypeError):
        return 'text-base-content/40'

    if metric_type == 'util':
        # Higher is worse (CPU, Mem, Disk)
        if v > 80: return 'text-error'
        if v > 60: return 'text-warning'
        if v > 40: return 'text-info'
        if v > 20: return 'text-success'
        return 'text-base-content/40'
    elif metric_type == 'util_inv':
        # Higher is better (GPU Util)
        if v > 95: return 'text-error'   # throttling
        if v > 80: return 'text-warning'  # hot
        if v > 50: return 'text-success'  # working
        if v > 20: return 'text-info'     # light
        return 'text-base-content/40'    # idle
    elif metric_type == 'temp':
        if v > 85: return 'text-error'
        if v > 70: return 'text-warning'
        if v > 50: return 'text-success'
        return 'text-info'
    elif metric_type == 'fan':
        if v > 90: return 'text-error'
        if v > 60: return 'text-warning'
        if v > 30: return 'text-success'
        return 'text-info'
    elif metric_type == 'error':
        if v > 0: return 'text-error'
        return 'text-success'
    return 'text-base-content/40'
```

**Usage in template:**
```html
<span class="{{ cpu_util|metric_color:'util' }}">{{ cpu_util }}%</span>
<span class="{{ gpu_util|metric_color:'util_inv' }}">{{ gpu_util }}%</span>
<span class="{{ gpu_temp|metric_color:'temp' }}">{{ gpu_temp }}°C</span>
```

### **2. `metric_value_bg` filter (single value → bg color class)**

Same thresholds, but returns background instead of text. For colored cells.

```python
@register.filter
def metric_bg(value, metric_type='util'):
    """Return DaisyUI semantic BG color for cell background."""
    color = metric_color(value, metric_type)
    # text-error -> bg-error/20
    return color.replace('text-', 'bg-') + '/20'
```

### **3. `multi_value_cell` partial (multi-GPU/disk cell)**

For cells that show multiple values (one per GPU or disk), each value gets
its own color. This is the **Grafana Pod Task Manager** pattern.

```html
{# _multi_value_cell.html #}
{# Renders a list of values with per-value color coding. #}
{# usage: {% include "dashboard/_multi_value_cell.html" with values=snapshot.gpu_temps_json metric_type="temp" suffix="°C" %} #}
{% if values %}
<div class="flex flex-wrap gap-1">
    {% for v in values %}
    <span class="px-1.5 py-0.5 rounded text-xs font-mono
                 {{ v|metric_color:metric_type }}
                 {{ v|metric_bg:metric_type }}">
        {% if v is not None %}{{ v|floatformat:0 }}{% if suffix %}{{ suffix }}{% endif %}{% else %}—{% endif %}
    </span>
    {% endfor %}
</div>
{% else %}
<span class="text-base-content/40">—</span>
{% endif %}
```

**Result for a rig with 4 GPUs at 55°C, 72°C, 88°C, 95°C:**
```
[55]  [72]  [88]  [95]
info  warn  warn  err   ← each chip is its own color
```

**Result for a rig with 5 disks at 55%, 73%, 75%, 72%, 0%:**
```
[55]  [73]  [75]  [72]  [0]
norm  warn  warn  warn  idle
```

This pattern is **scannable, color-coded, and compact** — exactly what
"at a glance" requires.

---

## 📋 Final Column Layout (16 columns + sticky header)

After research, here's the recommended Fleet Overview layout. The user
explicitly said "many parameters" so we **should not** artificially limit to 12.
Industry standard (Grafana, Datadog, NetData) is 15-20+ columns for fleet views.

### **Column Order (scannability order)**

| # | Column | Width | Source | Color Logic |
|---|--------|-------|--------|-------------|
| 1 | **Rig Name** | `w-40` | `rig.name` | — (link) |
| 2 | **Tags** | `w-24` | `rig.tags` | tag color from user |
| 3 | **Status** | `w-20` | `rig.status` | badge-success/warning/error |
| 4 | **Job** | `w-8` | `snapshot.has_active_job` | status-success / status-error dot |
| 5 | **GPU Model** | `w-28` | `snapshot.gpu_models_json` + `gpu_count` | — |
| 6 | **GPU Util** | `w-24` | `snapshot.gpu_utils_json` | util_inv, per-GPU chip |
| 7 | **GPU Temp** | `w-24` | `snapshot.gpu_temps_json` | temp, per-GPU chip |
| 8 | **GPU Fan** | `w-20` | `snapshot.gpu_fans_json` | fan, per-GPU chip |
| 9 | **GPU Power** | `w-24` | `snapshot.gpu_power_draws_json` | — (just W number) |
| 10 | **VRAM** | `w-24` | `snapshot.gpu_mem_util_pcts_json` | util, per-GPU chip |
| 11 | **CPU** | `w-16` | `snapshot.cpu_utilization_pct` | util, single value |
| 12 | **CPU Temp** | `w-16` | `snapshot.cpu_temp_c` | temp, single value |
| 13 | **Mem** | `w-16` | `snapshot.mem_used_pct` (computed) | util, single value |
| 14 | **Disk** | `w-24` | `snapshot.storage_usage_pcts_json` | util, per-disk chip |
| 15 | **Net** | `w-20` | `network_rx/tx_bytes_json` | — (MB/s or sum) |
| 16 | **Power** | `w-16` | `snapshot.power_total_w` | — (W number) |
| 17 | **Last Seen** | `w-20` | `rig.last_seen` | — (relative time) |
| 18 | **Agent** | `w-16` | `snapshot.agent_version` | — |

**Total: 18 columns** at standard density. At 1440px wide screen, each
column averages ~70px. Tight but readable.

### **Compact Mode (12 columns for narrower screens)**

For mobile / side-by-side views, hide secondary columns via `data-` toggle:

| # | Column | Same as above |
|---|--------|----------------|
| 1-5 | Rig, Tags, Status, Job, GPU Model | (1-5) |
| 6-9 | Util, Temp, VRAM, Power (all GPU summary) | (collapsed to "GPU: 87%/72°C/45%/180W") |
| 10 | CPU + Temp (combined) | (collapsed) |
| 11 | Mem + Disk (combined) | (collapsed) |
| 12 | Last Seen | (17) |

User can toggle via `data-compact="true"` on table. Persists in localStorage.

### **Sticky Header (Critical for "At a Glance")**

```html
<thead class="sticky top-0 z-10 bg-base-200">
  <tr>
    <th class="text-xs font-semibold">Rig</th>
    <th class="text-xs font-semibold">Tags</th>
    ...
  </tr>
</thead>
```

Plus first column (Rig Name) sticky on horizontal scroll:
```html
<td class="sticky left-0 bg-base-100 ...">
```

This way, even when scrolling 100 rigs vertically or wide tables horizontally,
the user always sees what column is what and what rig is what.

---

## 🧪 Visual Mockup (DaisyUI Implementation)

```html
<table class="table table-pin-rows table-pin-cols table-sm">
  <thead>
    <tr class="bg-base-200">
      <th class="sticky left-0 bg-base-200 z-20">Rig</th>
      <th>Tags</th>
      <th>Status</th>
      <th>Job</th>
      <th>GPU</th>
      <th>Util</th>
      <th>Temp</th>
      <th>Fan</th>
      <th>Pwr</th>
      <th>VRAM</th>
      <th>CPU</th>
      <th>CPU°C</th>
      <th>Mem</th>
      <th>Disk</th>
      <th>Net</th>
      <th>W</th>
      <th>Seen</th>
    </tr>
  </thead>
  <tbody>
    {% for item in rig_data %}
    {% with rig=item.rig snap=item.snapshot %}
    <tr class="hover:bg-base-200/50">
      <!-- 1. Rig Name (sticky left) -->
      <td class="sticky left-0 bg-base-100 font-semibold">
        <a href="{% url 'dashboard:rig-detail' rig.uuid %}"
           class="link link-primary">{{ rig.name|truncatechars:16 }}</a>
      </td>

      <!-- 2. Tags -->
      <td>
        {% for tag in rig.tags.all %}
        <span class="badge badge-sm" style="background:{{ tag.color }}22; color:{{ tag.color }}">{{ tag.name }}</span>
        {% endfor %}
      </td>

      <!-- 3. Status -->
      <td>
        {% if rig.status == 'online' %}
          <span class="badge badge-success badge-sm">●</span>
        {% elif rig.status == 'stale' %}
          <span class="badge badge-warning badge-sm">●</span>
        {% else %}
          <span class="badge badge-error badge-sm">●</span>
        {% endif %}
      </td>

      <!-- 4. Job -->
      <td>
        {% if snap.has_active_job %}
          <span class="status status-success" title="Active job"></span>
        {% else %}
          <span class="status status-error" title="No active job"></span>
        {% endif %}
      </td>

      <!-- 5. GPU Model (e.g. "RTX 3060 ×4") -->
      <td>
        <span class="text-xs font-medium">{{ snap.gpu_models_json|compact_models }}</span>
      </td>

      <!-- 6. GPU Util (per-GPU chips, inverted color) -->
      <td>
        {% include "dashboard/_multi_value_cell.html"
             with values=snap.gpu_utils_json metric_type="util_inv" suffix="%" %}
      </td>

      <!-- 7. GPU Temp (per-GPU chips) -->
      <td>
        {% include "dashboard/_multi_value_cell.html"
             with values=snap.gpu_temps_json metric_type="temp" suffix="°C" %}
      </td>

      <!-- 8. GPU Fan (per-GPU chips) -->
      <td>
        {% include "dashboard/_multi_value_cell.html"
             with values=snap.gpu_fans_json metric_type="fan" suffix="%" %}
      </td>

      <!-- 9. GPU Power (per-GPU, just number) -->
      <td>
        {% include "dashboard/_multi_value_cell.html"
             with values=snap.gpu_power_draws_json metric_type="util" suffix="W" %}
      </td>

      <!-- 10. VRAM (per-GPU chips) -->
      <td>
        {% include "dashboard/_multi_value_cell.html"
             with values=snap.gpu_mem_util_pcts_json metric_type="util" suffix="%" %}
      </td>

      <!-- 11. CPU Util (single value) -->
      <td>
        <span class="px-1.5 py-0.5 rounded font-mono
                     {{ snap.cpu_utilization_pct|metric_bg:'util' }}
                     {{ snap.cpu_utilization_pct|metric_color:'util' }}">
          {{ snap.cpu_utilization_pct|floatformat:0 }}
        </span>
      </td>

      <!-- 12. CPU Temp (single value) -->
      <td>
        <span class="px-1.5 py-0.5 rounded font-mono
                     {{ snap.cpu_temp_c|metric_bg:'temp' }}
                     {{ snap.cpu_temp_c|metric_color:'temp' }}">
          {{ snap.cpu_temp_c|floatformat:0 }}°
        </span>
      </td>

      <!-- 13. Mem (used/total, single value) -->
      <td>
        <span class="px-1.5 py-0.5 rounded font-mono
                     {{ mem_pct|metric_bg:'util' }}
                     {{ mem_pct|metric_color:'util' }}">
          {{ mem_pct|floatformat:0 }}%
        </span>
      </td>

      <!-- 14. Disk (per-disk chips) -->
      <td>
        {% include "dashboard/_multi_value_cell.html"
             with values=snap.storage_usage_pcts_json metric_type="util" suffix="%" %}
      </td>

      <!-- 15. Net (sum of rx+tx, with errors) -->
      <td>
        {% include "dashboard/_net_cell.html" with net_data=item.net_summary %}
      </td>

      <!-- 16. Power (single value) -->
      <td>
        <span class="font-mono text-info">{{ snap.power_total_w|floatformat:0 }}W</span>
      </td>

      <!-- 17. Last Seen -->
      <td class="text-base-content/50 text-xs">
        {{ rig.last_seen|last_seen_short }}
      </td>
    </tr>
    {% endwith %}
    {% endfor %}
  </tbody>
</table>
```

### **Result: One Rig Row**

For a rig with 4 GPUs (RTX 3090) at various states:

```
Name:    gaming-rig-01
Tags:    [gaming] [prod]
Status:  ●
Job:     ●
GPU:     RTX 3090 ×4
Util:    [45]  [67]  [88]  [95]    ← 4 chips, each colored
Temp:    [62]  [71]  [78]  [86]    ← 4 chips, increasing severity
Fan:     [35]  [48]  [72]  [95]    ← 4 chips
Pwr:     [180W] [220W] [290W] [340W]
VRAM:    [42%] [68%] [85%] [94%]   ← 4 chips
CPU:     [73]                        ← single chip
CPU°C:   [78]
Mem:     [62%]
Disk:    [55%] [73%] [75%] [72%] [0%]  ← 5 chips (5 disks)
Net:     12M/s
W:       1030W
Seen:    3s ago
```

**Total: 18 columns × 1 row = all info visible in 1 glance.**

---

## 🚀 Implementation (What Goes in the PR)

### **1. Backend (Python)**

**`gpu_monitor/dashboard/templatetags/metric_colors.py`** (NEW file)
- `metric_color` filter (single value → text color)
- `metric_bg` filter (single value → bg color)
- `compact_models` filter (["RTX 3060", "RTX 3060"] → "RTX 3060 ×2")
- `net_summary` filter (rx/tx arrays → "12M/s" or "1.2G/s")

**`gpu_monitor/dashboard/views.py`** (modify `rig_list`)
```python
# In _build_rig_data (per-rig loop), pre-compute:
'disk_max_pct': max([v for v in snap.storage_usage_pcts_json or [] if v is not None], default=None),
'mem_pct': (snap.mem_used_bytes / snap.mem_total_bytes * 100) if snap.mem_total_bytes else None,
'net_summary': _summarize_network(snap),  # pre-compute total + max interface
'cpu_freq_ghz': snap.cpu_freq_current_mhz / 1000 if snap.cpu_freq_current_mhz else None,
```

### **2. Frontend (Templates)**

**`gpu_monitor/templates/dashboard/_multi_value_cell.html`** (NEW partial)
- Renders list of values as colored chips (the per-GPU/per-disk pattern)

**`gpu_monitor/templates/dashboard/_rig_table.html`** (REWRITE)
- Use DaisyUI `table table-pin-rows table-pin-cols table-sm`
- Replace 15 existing columns with 18 new columns
- All color logic via `metric_color` / `metric_bg` filters
- No raw `text-red-400` / `bg-green-900/50` — all semantic

**`gpu_monitor/templates/dashboard/_rig_status_badge.html`** (use `badge`)
- Replace `<span class="badge-online">` with `<span class="badge badge-success">`

**`gpu_monitor/templates/dashboard/_net_cell.html`** (NEW partial)
- Shows network summary: "12M/s RX · 3M/s TX" with errors colored

### **3. Base Layout (CSS)**

**`base.html`** (modify)
- Replace `cdn.tailwindcss.com` script with DaisyUI 5 + Tailwind 4 browser
- Set `data-theme="dark"` on `<html>` (default dark, user can switch)

---

## ⚠️ Constraints Preserved

1. **No new backend queries** — all data is already in `LatestSnapshot`
   (already optimized in `fix/chart-data-view-perf`)
2. **No regressions** — all 33 unit tests still pass
3. **No build step** — DaisyUI loads from CDN, no npm/webpack
4. **Theme support** — DaisyUI 5 themes auto-apply to `bg-base-200`, `text-error`, etc.
5. **Accessibility** — DaisyUI `badge`, `status`, `progress` have ARIA built-in
6. **Mobile responsive** — `table-sm` + sticky columns work down to 768px

---

## 📈 Comparison: Before vs After

| Aspect | Before | After |
|--------|--------|-------|
| Visible columns | 15 | 18 (12 in compact mode) |
| Color threshold copies | 10+ inline | 1 filter (`metric_color`) |
| Sticky header | No | Yes (table-pin-rows) |
| Sticky first column | No | Yes (table-pin-cols) |
| Per-value color coding | Only Disk, CPU Util | All 18 columns |
| Color consistency | 5+ different Tailwind greens | 1 DaisyUI semantic palette |
| Multi-GPU cell | Space-separated text | Per-GPU colored chips |
| Theme support | None (always dark) | 35 DaisyUI themes |
| Mobile scroll | Horizontal only | Sticky cols + horizontal scroll |
| Page size (HTML) | ~9KB | ~6KB (more semantic) |

---

## ✅ Acceptance Criteria

The redesigned Fleet Overview is "done" when:

1. **All 18 columns visible** at 1440px width without horizontal scroll
2. **All 12 critical columns visible** at 1280px (compact mode)
3. **Sticky header** stays at top during vertical scroll
4. **Sticky first column** (Rig Name) stays at left during horizontal scroll
5. **Every numeric value is color-coded** based on its threshold
6. **Multi-GPU/multi-disk cells show individual chips**, each colored independently
7. **Color is consistent** — same metric, same color, across pages
8. **DaisyUI theme switcher** in user menu (5 themes available)
9. **All 33 unit tests pass**
10. **No new DB queries** introduced (still 0 time-series queries per page load)
11. **Page renders in < 500ms** with 100 rigs (current: < 200ms)

---

## 🗓️ Implementation Phases

### **Phase 1: Foundation (small PR)**
- Add `metric_colors.py` with `metric_color`, `metric_bg`, `compact_models` filters
- Add `_multi_value_cell.html` partial
- Add DaisyUI CDN to `base.html`
- Verify all pages still render

### **Phase 2: Fleet Overview (medium PR)**
- Rewrite `_rig_table.html` with 18 columns + sticky header/column
- Add `_net_cell.html` partial
- Add `data-compact` toggle in localStorage
- Update `_rig_status_badge.html` to use `badge` component
- Pre-compute aggregations in `views.py` (mem_pct, net_summary, etc.)

### **Phase 3: Live Metrics (medium PR)**
- Replace `_metrics_cards.html` card wrappers with `card bg-base-200`
- Add `radial-progress` hero card
- Convert power section to `stats` horizontal layout
- Use `progress` for all bar visualizations

### **Phase 4: Rig Detail (large PR)**
- Convert top tab bar to `tabs tabs-border` (drop 100+ lines of JS)
- Wrap each of 21 charts in `card` partial
- Add skeleton loading state

### **Phase 5: Polish (small PR)**
- Add theme switcher in user dropdown
- Convert timeline (container history, error history)
- Add Toast component for save/rename feedback
- Update architecture doc

---

**Branch:** `feat/ui-ux-research` (research), pending merge to `feat/daisyui-professional-redesign` for implementation.
**PR URL:** https://github.com/dawmro/GPU-Rig-Monitoring-Platform/pull/new/feat/ui-ux-research

Ready to proceed with implementation.
