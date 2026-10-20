# Plan: Fix Container Details Text Wrapping

## Problem
In the Container Details section of live metrics (`_metrics_cards.html`), text in various fields overflows to the right instead of wrapping to new lines. This happens because:
- `truncate` class = `overflow-hidden whitespace-nowrap text-overflow-ellipsis` (truncates but doesn't wrap)
- `whitespace-nowrap` explicitly prevents wrapping
- Long strings without spaces (like container IDs, digests, env values, label values) don't break naturally

## Solution
Replace `truncate` and `whitespace-nowrap` with `break-words` (or `break-all`) to force wrapping even for continuous strings without spaces.

## Files to Modify
- `gpu_monitor/templates/dashboard/_metrics_cards.html` (lines 879-1152)

## Specific Changes

### 1. Labels (line 965)
**Current:** `<div class="truncate">{{ k }}: {{ v }}</div>`
**Fixed:** `<div class="break-words">{{ k }}: {{ v }}</div>`

### 2. Annotations (line 976)
**Current:** `<div class="truncate">{{ k|cut:"org.opencontainers.image."|cut:"com.docker." }}: {{ v }}</div>`
**Fixed:** `<div class="break-words">{{ k|cut:"org.opencontainers.image."|cut:"com.docker." }}: {{ v }}</div>`

### 3. Mounts (line 943)
**Current:** `<div class="truncate">{{ m.source }} → {{ m.destination }} ({{ m.type }}{% if m.rw %}, rw{% endif %})</div>`
**Fixed:** `<div class="break-words">{{ m.source }} → {{ m.destination }} ({{ m.type }}{% if m.rw %}, rw{% endif %})</div>`

### 4. Port Bindings (line 996)
**Current:** `<div class="truncate">{{ host_port }} → {% for b in bindings %}{{ b.HostIp }}:{{ b.HostPort }}{% if not forloop.last %}, {% endif %}{% endfor %}</div>`
**Fixed:** `<div class="break-words">{{ host_port }} → {% for b in bindings %}{{ b.HostIp }}:{{ b.HostPort }}{% if not forloop.last %}, {% endif %}{% endfor %}</div>`

### 5. Resource Limits (line 1007)
**Current:** `<div class="truncate">{{ k }}: {{ v }}</div>`
**Fixed:** `<div class="break-words">{{ k }}: {{ v }}</div>`

### 6. Reservations (line 1027)
**Current:** `<div class="truncate">{{ k }}: {{ v }}</div>`
**Fixed:** `<div class="break-words">{{ k }}: {{ v }}</div>`

### 7. Security (line 1098)
**Current:** `<div class="truncate">{{ k }}: {{ v }}</div>`
**Fixed:** `<div class="break-words">{{ k }}: {{ v }}</div>`

### 8. DNS (line 1109)
**Current:** `<div class="truncate">{{ k }}: {{ v }}</div>`
**Fixed:** `<div class="break-words">{{ k }}: {{ v }}</div>`

### 9. Env (lines 1118-1121)
**Current:** 
```html
<dd class="text-gray-200 max-w-full overflow-x-auto">
    {% for k, v in c.manifest.env.items %}
    <div class="whitespace-nowrap"><span class="text-gray-500">{{ k }}</span>=<span class="text-green-300">{{ v }}</span></div>
    {% endfor %}
</dd>
```
**Fixed:**
```html
<dd class="text-gray-200 max-w-full">
    {% for k, v in c.manifest.env.items %}
    <div class="break-words"><span class="text-gray-500">{{ k }}</span>=<span class="text-green-300">{{ v }}</span></div>
    {% endfor %}
</dd>
```

## Testing
- Verify text wraps in all fields (labels, annotations, mounts, port bindings, resources, reservations, security, DNS, env)
- Test with long strings without spaces (container digests, long env values, etc.)
- Ensure layout doesn't break on mobile/responsive views

## Branch
`plan/fix-container-details-text-wrap`