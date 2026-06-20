# API Key Transfer — Name Collision Deep Analysis

## The Collision Suffix Problem

### Current approach: `-<unixtimestamp>` suffix
Format: `base_name-1719000000`

Regex to strip old suffix: `r'-\d{10,}$'`

### Scenario: Collision happens during transfer

1. User A has key "rack-key"
2. Transfer to User B → "rack-key-1719000000" (no collision)
3. Transfer to User C → "rack-key-1719000100" (stripped old, appended new) ✅
4. But if User C already has "rack-key-1719000200" → collision → "rack-key-1719000200-1"
5. Transfer to User D → regex tries to strip suffix from "rack-key-1719000200-1"
   - End of string: `-1` → only 1 digit → NO MATCH
   - Base = "rack-key-1719000200-1"
   - New name = "rack-key-1719000200-1-1719000300" ❌ NAME GROWS!

This is a real problem. The collision suffix `-1` prevents the regex from matching the timestamp.

### Root Cause
The collision suffix `-N` (where N is a small counter) is indistinguishable from a regular name suffix. The regex can't tell whether the trailing digits are OUR timestamp or part of the user's original name.

---

## Possible Solutions

### Solution 1: Use a different separator for collision suffix
Instead of `-1`, use `--` (double dash) as separator:
- `rack-key-1719000200--1`
- Regex to strip: `r'-\d{10,}(--\d+)*$'` — matches timestamp + optional collision suffixes

**Problems:**
- `--1` looks weird
- Still grows with multiple collisions
- Double dash might be confusing

### Solution 2: Store the original name separately
Add `original_name` field to ApiKey. Always use `original_name` as the base for transfer naming.

```python
class ApiKey(models.Model):
    name = models.CharField(max_length=255)  # current display name
    original_name = models.CharField(max_length=255)  # name at creation time
```

On transfer:
```python
base_name = key.original_name  # Always clean
new_name = f"{base_name}-{timestamp}"
```

**Advantages:**
- Name never grows from transfers
- Collision suffix is always stripped cleanly
- Original intent preserved

**Problems:**
- Adds a field to the model
- `original_name` might not match current `name` if user renamed the key themselves
- What if user deliberately renamed to include a timestamp-like suffix?

### Solution 3: Don't strip — just append with delimiter change
Use a character that's unlikely in key names as the transfer marker, e.g., `~`:

- Original: `rack-key`
- 1st transfer: `rack-key~1719000000`
- 2nd transfer: `rack-key~1719000100` (strip `~1719000000`, append `~1719000100`)
- Collision: `rack-key~1719000100~1`
- 3rd transfer: strip `~1719000100~1`, append `~1719000200`

Regex: `r'~\d{10,}(~\d+)*$'` — matches `~` + 10+ digits + optional `~N` collision suffixes

**Advantages:**
- `~` is rare in key names
- Easy to strip completely
- Clean naming history

**Problems:**
- `~` might be confusing to users
- Still need collision handling

### Solution 4: Just append timestamp, never strip
- Original: `rack-key`
- 1st transfer: `rack-key-1719000000`
- 2nd transfer: `rack-key-1719000000-1719000100`
- 3rd transfer: `rack-key-1719000000-1719000100-1719000200`

**Advantages:**
- Simplest implementation
- Full audit trail in the name
- No regex parsing needed

**Problems:**
- Names grow unbounded
- Hard to read after multiple transfers

### Solution 5: Original name + transfer count suffix
Instead of timestamp, use incrementing counter:
- Original: `rack-key`
- 1st transfer: `rack-key (transfer 1)`
- 2nd transfer: `rack-key (transfer 2)`
- Collision: `rack-key (transfer 2) (2)`

**Advantages:**
- Human-readable
- Always clean base name
- Counter shows transfer history

**Problems:**
- Still need collision handling
- `(transfer N)` format might conflict with user's own naming

### Solution 6: Separate transfer_name field (RECOMMENDED)
Store the "clean" name separately from the display name:

```python
class ApiKey(models.Model):
    name = models.CharField(max_length=255)  # display name, user-editable
    base_name = models.CharField(max_length=255)  # auto-managed, stripped on transfer
    transfer_count = models.PositiveIntegerField(default=0)
```

On creation:
```python
key.name = name
key.base_name = name
```

On transfer:
```python
base = key.base_name  # Always clean, never has transfer suffixes
timestamp = int(time.time())
new_name = f"{base}-{timestamp}"

# Handle collision
counter = 0
final_name = new_name
while ApiKey.objects.filter(user=target_user, name=final_name).exists():
    counter += 1
    final_name = f"{new_name}-{counter}"

key.name = final_name
key.base_name = base  # Keep base_name clean!
key.transfer_count += 1
key.save()
```

**Name evolution:**
| Transfer | name | base_name | transfer_count |
|---|---|---|---|
| Create | `rack-key` | `rack-key` | 0 |
| 1st transfer | `rack-key-1719000000` | `rack-key` | 1 |
| 2nd transfer | `rack-key-1719000100` | `rack-key` | 2 |
| Collision transfer | `rack-key-1719000200-1` | `rack-key` | 3 |
| 4th transfer | `rack-key-1719000300` | `rack-key` | 4 |

**Advantages:**
- base_name NEVER grows — always clean
- Collision suffix is always stripped because we use base_name
- transfer_count shows history
- Full audit trail possible

**Disadvantages:**
- Adds base_name field
- Slightly more complex logic

### Even better: Drop the timestamp entirely

The timestamp in the name adds complexity without much value. The audit log already records when transfers happened. Instead:

```python
On transfer:
    base = key.base_name
    new_name = base  # Try the clean name first
    
    # Handle collision with simple counter
    counter = 1
    final_name = new_name
    while ApiKey.objects.filter(user=target_user, name=final_name).exists():
        final_name = f"{base}-{counter}"
        counter += 1
    
    key.name = final_name
    key.transfer_count += 1
```

**Name evolution (no timestamp):**
| Transfer | name | base_name | transfer_count |
|---|---|---|---|
| Create | `rack-key` | `rack-key` | 0 |
| 1st transfer | `rack-key` | `rack-key` | 1 |
| 2nd transfer | `rack-key` | `rack-key` | 2 |
| Collision (target has "rack-key") | `rack-key-1` | `rack-key` | 3 |
| 4th transfer to new user | `rack-key` | `rack-key` | 4 |

**Wait — this has a problem too!** If User B receives "rack-key" and then transfers to User C, User C also gets "rack-key". But if User B still has "rack-key (transfer 1)" from their OWN keys, there's no collision because `base_name` is only used for the transferred key.

Actually this works because:
- User B receives key with name="rack-key", base_name="rack-key"
- User B transfers to User C: tries "rack-key" → if User C has it, tries "rack-key-1", etc.
- base_name stays "rack-key" forever

**This is the cleanest approach!**

---

## RECOMMENDED FINAL DESIGN

### Model Changes
```python
class ApiKey(models.Model):
    # ... existing fields ...
    base_name = models.CharField(max_length=255)  # Clean name, stripped on each transfer
    transfer_count = models.PositiveIntegerField(default=0)
```

### On Creation
```python
key.name = name
key.base_name = name
```

### On Transfer
```python
def _generate_transfer_name(base_name, target_user):
    """Generate unique name for transferred key in target user's namespace."""
    new_name = base_name  # Try clean name first
    
    # Handle collision with incrementing counter
    counter = 1
    final_name = new_name
    while ApiKey.objects.filter(user=target_user, name=final_name).exists():
        final_name = f"{base_name}-{counter}"
        counter += 1
    
    # Truncate if needed (base_name could be up to 255 chars)
    return final_name[:255]

# In transfer view:
for key in keys_to_transfer:
    new_name = _generate_transfer_name(key.base_name, target_user)
    
    key.user = target_user
    key.name = new_name
    key.base_name = key.base_name  # Unchanged — stays clean!
    key.transfer_count += 1
    key.save()
    
    # CRITICAL: Update rig ownership
    Rig.objects.filter(enrolled_by_api_key=key).update(owner=target_user)
```

### Display in Template
```html
<span class="font-medium">{{ key.name }}</span>
{% if key.transfer_count > 0 %}
<span class="text-xs text-gray-500" title="Transferred {{ key.transfer_count }} time(s)">
  ↻ {{ key.transfer_count }}
</span>
{% endif %}
```

### Example Name Evolution

| Event | name | base_name | transfer_count |
|---|---|---|---|
| User A creates | `rack-key` | `rack-key` | 0 |
| A → B transfer | `rack-key` | `rack-key` | 1 |
| B → C transfer | `rack-key` | `rack-key` | 2 |
| C → D (D has "rack-key") | `rack-key-1` | `rack-key` | 3 |
| D → E transfer | `rack-key` | `rack-key` | 4 |

**Key insight:** The collision suffix `-1`, `-2`, etc. is ALWAYS stripped on next transfer because we use `base_name` (which is clean) and only append a NEW counter. The old collision suffix is never carried forward.