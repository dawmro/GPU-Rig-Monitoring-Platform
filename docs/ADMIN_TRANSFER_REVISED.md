# Admin Key Transfer — Implementation Reference

## Current Implementation

### URL
`/accounts/admin/transfer-keys/` (staff-only)

### Access Control
- Only `is_staff` users can access
- Regular users get error message and redirect to API keys page

### Flow

#### Step 1: Select Source User
- Dropdown with all users (ordered by email)
- **Auto-submits on change** — no "Load Keys" button needed
- Uses `onchange="window.location.href='?source_user_id='+this.value"`

#### Step 2: Select Key(s) — Separate Bordered Section
- Shows all keys belonging to source user
- Checkboxes for multi-select
- Each key shows: name, status, rig count, creation date, enrolled rig names
- Scrollable list (max 400px height)

#### Step 3: Select Target User — Separate Bordered Section
- Dropdown with all users except source user
- Warning message: "This will transfer the selected key(s) and all their enrolled rigs..."
- Transfer button

### Implementation Details

**View:** `admin_transfer_keys()` in `accounts/views.py`
- Handles both GET (load keys) and POST (execute transfer)
- On GET with `source_user_id`: loads keys for that user
- On POST: validates, transfers keys, updates rig ownership, clears rig tags

**Template:** `templates/accounts/admin_transfer_keys.html`
- Single `<form>` wraps both Step 2 and Step 3
- Checkboxes are inside the form (no JavaScript needed)
- Step 2 and Step 3 are separate `<div>` elements with their own borders

**Name Collision Handling:** `_generate_transfer_name()` in `accounts/views.py`
- Uses `base_name` (always clean, never has transfer suffixes)
- Tries clean name first, then appends incrementing counter (`-1`, `-2`, etc.)
- No timestamps in names

### Edge Cases Handled

| Case | Handling |
|---|---|
| Source = Target | Reject with error message |
| No keys selected | Reject with error message |
| No target selected | Reject with error message |
| Key has 0 rigs | Allowed (no rigs affected) |
| Key has N rigs | Transfer key + all N rigs with warning |
| Target has same key name | Auto-append counter via `_generate_transfer_name` |
| Source user has no keys | Show info message |
| Non-staff access | Error message + redirect |
| Concurrent transfers | Re-filters keys by source_user before transfer |
| Name collision chains | `base_name` stays clean, only `name` gets suffix |

### Security
- Staff-only access (`is_staff` check in view)
- Cannot transfer to self
- Keys filtered by `user=source_user` (prevents forged POST)
- All transfers logged via `log_audit_event`
- CSRF protection (standard Django)

### Database Impact
- Updates `key.user`, `key.name`, `key.transfer_count`
- Updates `rig.owner` for all enrolled rigs
- Clears `rig.tags` for all enrolled rigs (tags are per-user)
- Single `rig.save()` call combining `last_seen`, `status`, and optionally `enrolled_by_api_key`

### Name Evolution Example

| Event | `name` | `base_name` | `transfer_count` |
|---|---|---|---|
| User A creates | `rack-key` | `rack-key` | 0 |
| A → B transfer | `rack-key` | `rack-key` | 1 |
| B → C transfer | `rack-key` | `rack-key` | 2 |
| C → D (D has "rack-key") | `rack-key-1` | `rack-key` | 3 |
| D → E transfer | `rack-key` | `rack-key` | 4 |

**Key insight:** `base_name` stays clean forever. Collision suffix (`-1`, `-2`) is only in `name`, never in `base_name`. On next transfer, the old collision suffix is automatically discarded.
