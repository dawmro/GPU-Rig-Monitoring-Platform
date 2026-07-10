# "Loaded @ <time>" vs "Refreshed @ <time>" — Implementation Plan

## Current Behavior

**File:** `gpu_monitor/templates/base.html` (lines 73-86)

```javascript
document.body.addEventListener('htmx:afterSwap', function(evt) {
    var targetId = evt.detail && evt.detail.target && evt.detail.target.id;
    if (!targetId) return;
    var d = new Date();
    var ts = String(d.getHours()).padStart(2,'0') + ':' +
             String(d.getMinutes()).padStart(2,'0') + ':' +
             String(d.getSeconds()).padStart(2,'0');
    var clock = document.getElementById('global-refresh-clock');
    if (clock) clock.textContent = 'Refreshed @ ' + ts;
    var fc = document.getElementById('rig-table-container-clock');
    if (fc) fc.textContent = 'Refreshed @ ' + ts;
});
```

**Current state:**
- Single handler for ALL HTMX swaps
- Always shows "Refreshed @ HH:MM:SS"
- Two clocks updated: `#global-refresh-clock` (rig detail) and `#rig-table-container-clock` (fleet overview)

---

## Requirements

| Scenario | Fleet Overview (`rig_list.html`) | Live Metrics (`rig_detail.html`) |
|----------|----------------------------------|----------------------------------|
| Initial page load (full HTML) | **Loaded @ HH:MM:SS** | **Loaded @ HH:MM:SS** |
| Manual browser reload (F5) | **Loaded @ HH:MM:SS** | **Loaded @ HH:MM:SS** |
| HTMX partial swap (30s poll) | **Refreshed @ HH:MM:SS** | **Refreshed @ HH:MM:SS** |
| Tab switch to Charts (manual) | N/A | **Refreshed @ HH:MM:SS** |

---

## Implementation Plan

### Approach: Distinguish Initial Load vs HTMX Swap

**Key insight:** `htmx:afterSwap` fires for HTMX-initiated swaps only. Initial page load does NOT trigger this event.

**Strategy:**
1. On page load (DOMContentLoaded), set clock to "Loaded @ HH:MM:SS"
2. On `htmx:afterSwap`, set clock to "Refreshed @ HH:MM:SS"
3. Each clock element manages its own label independently

### File Changes

#### 1. `base.html` — Core clock logic

**Changes:**
- Add `DOMContentLoaded` listener to set initial "Loaded @"
- Keep existing `htmx:afterSwap` for "Refreshed @"
- Make clock updates target-specific (not global)

```javascript
// On initial page load — set "Loaded @"
document.addEventListener('DOMContentLoaded', function() {
    var d = new Date();
    var ts = String(d.getHours()).padStart(2,'0') + ':' +
             String(d.getMinutes()).padStart(2,'0') + ':' +
             String(d.getSeconds()).padStart(2,'0');
    // Set all clock elements to "Loaded @"
    document.querySelectorAll('[id$="-clock"]').forEach(function(clock) {
        clock.textContent = 'Loaded @ ' + ts;
    });
});

// On HTMX swap — set "Refreshed @"
document.body.addEventListener('htmx:afterSwap', function(evt) {
    var targetId = evt.detail && evt.detail.target && evt.detail.target.id;
    if (!targetId) return;
    
    var d = new Date();
    var ts = String(d.getHours()).padStart(2,'0') + ':' +
             String(d.getMinutes()).padStart(2,'0') + ':' +
             String(d.getSeconds()).padStart(2,'0');
    
    // Only update the clock for the swapped target
    var clock = document.getElementById(targetId + '-clock');
    if (clock) clock.textContent = 'Refreshed @ ' + ts;
});
```

#### 2. `rig_list.html` — Fleet Overview clock

**Current:** `#rig-table-container-clock` (hardcoded in base.html)

**Change:** Clock ID must match HTMX target + `-clock` suffix
- Target: `#rig-table-container` (HTMX swaps this)
- Clock: `#rig-table-container-clock` ✓ (already matches)

**No template change needed** — current ID convention works.

#### 3. `rig_detail.html` — Rig Detail clocks

**Current clocks:**
- `#global-refresh-clock` (header status)
- `#metrics-container-clock` (Live Metrics tab)
- `#rig-status-container-clock` (status badge)

**HTMX targets that swap:**
- `#metrics-container` → needs `#metrics-container-clock`
- `#rig-status-container` → needs `#rig-status-container-clock`

**Current issue:** `#global-refresh-clock` doesn't match any HTMX target pattern

**Fix:** Rename `#global-refresh-clock` to match a target or handle separately

**Recommended:** 
- Keep `#global-refresh-clock` for initial "Loaded @"
- On HTMX swaps, update specific container clocks
- Header clock stays as "Loaded @" unless user manually refreshes page

#### 4. Clock element pattern

All clock spans should follow: `{target-id}-clock`

| HTMX Target | Clock ID | Location |
|-------------|----------|----------|
| `rig-table-container` | `rig-table-container-clock` | Fleet Overview |
| `metrics-container` | `metrics-container-clock` | Live Metrics |
| `rig-status-container` | `rig-status-container-clock` | Status Badge |

---

## Detailed Implementation Steps

### Step 1: Update `base.html`

Replace the current script block (lines 73-86) with new logic:

```html
<script>
// Initial page load — "Loaded @"
document.addEventListener('DOMContentLoaded', function() {
    var d = new Date();
    var ts = String(d.getHours()).padStart(2,'0') + ':' +
             String(d.getMinutes()).padStart(2,'0') + ':' +
             String(d.getSeconds()).padStart(2,'0');
    document.querySelectorAll('[id$="-clock"]').forEach(function(clock) {
        clock.textContent = 'Loaded @ ' + ts;
    });
});

// HTMX partial swap — "Refreshed @"
document.body.addEventListener('htmx:afterSwap', function(evt) {
    var targetId = evt.detail && evt.detail.target && evt.detail.target.id;
    if (!targetId) return;
    
    var d = new Date();
    var ts = String(d.getHours()).padStart(2,'0') + ':' +
             String(d.getMinutes()).padStart(2,'0') + ':' +
             String(d.getSeconds()).padStart(2,'0');
    
    var clock = document.getElementById(targetId + '-clock');
    if (clock) clock.textContent = 'Refreshed @ ' + ts;
});
</script>
```

### Step 2: Verify `rig_list.html` clock element

Current (line ~40 in rig_list.html):
```html
<div id="rig-table-container" hx-get="{% url 'dashboard:rig_list' %}" 
     hx-trigger="every 30s" hx-swap="innerHTML" 
     class="overflow-x-auto">
</div>
```

Clock is in `_rig_table.html` or similar — need to verify clock element exists with ID `rig-table-container-clock`.

### Step 3: Update `rig_detail.html` clocks

**Current header clock (line 47):**
```html
<span id="global-refresh-clock" class="text-gray-500 text-xs"></span>
```

**Live Metrics container (need to find):**
```html
<div id="metrics-container" hx-get="..." hx-trigger="every 30s" ...>
```

**Status badge container:**
```html
<div id="rig-status-container" hx-get="..." hx-trigger="every 15s" ...>
```

**Required changes:**
1. Add clock spans to each HTMX target container with matching `-clock` suffix
2. Header clock (`global-refresh-clock`) only gets "Loaded @" on initial load

### Step 4: Update partial templates

**`_metrics_cards.html`** — Add clock span inside `#metrics-container`:
```html
<div id="metrics-container" ...>
    <span id="metrics-container-clock" class="text-gray-500 text-xs"></span>
    ... existing content ...
</div>
```

**`_rig_status_badge.html`** — Add clock span inside `#rig-status-container`:
```html
<div id="rig-status-container" ...>
    <span id="rig-status-container-clock" class="text-gray-500 text-xs"></span>
    ... existing badge ...
</div>
```

---

## Edge Cases Handled

| Case | Behavior |
|------|----------|
| User opens page, waits 30s, HTMX fires | "Loaded @ T0" → "Refreshed @ T0+30s" |
| User opens page, immediately clicks tab | "Loaded @ T0" (header), "Refreshed @ T0+X" (tab content) |
| User F5 refreshes | Full reload → "Loaded @ T1" |
| User opens in new tab | Full load → "Loaded @ T0" |
| HTMX swap targets element without clock | Gracefully ignored (no clock element found) |
| Multiple HTMX targets swap simultaneously | Each gets its own clock updated |

---

## Testing Checklist

- [ ] Fleet Overview: Initial load shows "Loaded @"
- [ ] Fleet Overview: 30s poll shows "Refreshed @"
- [ ] Rig Detail header: Initial load shows "Loaded @"
- [ ] Rig Detail Live Metrics: Initial load shows "Loaded @"
- [ ] Rig Detail Live Metrics: 30s poll shows "Refreshed @"
- [ ] Rig Detail Status Badge: 15s poll shows "Refreshed @"
- [ ] Browser F5 reload: All clocks show "Loaded @"
- [ ] Tab switch to Charts: Shows "Refreshed @" when charts load
- [ ] Manual chart refresh (↻): Shows "Refreshed @"