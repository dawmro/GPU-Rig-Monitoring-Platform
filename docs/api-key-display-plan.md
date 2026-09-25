# Plan: Add last_used_at, id, base_name to API Keys Page Display

## Objective
Display three additional fields from the `ApiKey` model on the `/accounts/api-keys/` page:
1. **`last_used_at`** - Timestamp when key was last used for authentication
2. **`id`** - UUID primary key (for debugging/correlation)
3. **`base_name`** - Original clean name before transfer suffixes

## Current State
The `api_keys` view in `gpu_monitor/accounts/views.py` annotates `rig_count` and prefetches `enrolled_rigs`. The template `api_keys.html` + `_key_row.html` displays:
- name, transfer_count, is_active, rig_count, created_at, revoked_at, enrolled rigs list

## Changes Required

### 1. View (`gpu_monitor/accounts/views.py`)
- **Line 94-98**: The queryset already fetches all `ApiKey` fields via `ApiKey.objects.filter(...)`. No additional `select_related` or `annotate` needed since `id`, `base_name`, `last_used_at` are direct model fields.
- **No view changes required** - all three fields are already available on each `key` object in the template context.

### 2. Template: `_key_row.html` (partial for HTMX updates)
Add display for the three fields in the key info section (around line 10-14):

```html
<!-- Add after created_at/revoked_at line (line 14) -->
<div class="text-xs text-gray-400 font-mono">
    ID: {{ key.id }}
</div>
<div class="text-xs text-gray-400">
    Base name: {{ key.base_name|default:"—" }}
</div>
<div class="text-xs text-gray-400">
    Last used: {% if key.last_used_at %}{{ key.last_used_at|date:"Y-m-d H:i" }}{% else %}Never{% endif %}
</div>
```

### 3. Template: `api_keys.html` (full page load)
Same changes as `_key_row.html` in the `{% for key in keys %}` loop (around line 34).

## Display Format Decisions

| Field | Format | Rationale |
|---|---|---|
| `id` | Full UUID (36 chars), monospace | Copy-pasteable for debugging, log correlation |
| `base_name` | Plain text, show "—" if empty | Distinguishes from `name` after transfers |
| `last_used_at` | "Y-m-d H:i" format, "Never" if null | Consistent with created_at/revoked_at display |

## Visual Placement
Add as a new line group under the "Created/Revoked" line, before the rig count warnings. Keeps related metadata together.

## Security Considerations
- `last_used_at` is operational metadata, not sensitive
- `id` is a UUID (not guessable, not secret)
- `base_name` is user-provided name metadata
- **No secrets exposed** (key_hash, key_lookup, plaintext never in template)

## Testing Checklist
- [ ] Page loads with all three fields visible
- [ ] HTMX revoke/reactivate updates row correctly (uses `_key_row.html`)
- [ ] Empty `base_name` shows "—" not blank
- [ ] Null `last_used_at` shows "Never"
- [ ] UUID displays fully (not truncated)

## Files to Modify
1. `gpu_monitor/templates/accounts/_key_row.html` - HTMX partial
2. `gpu_monitor/templates/accounts/api_keys.html` - Full page

## Branch
`feat/api-key-display-fields` (to be created after plan approval)