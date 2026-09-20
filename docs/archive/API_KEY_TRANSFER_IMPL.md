# API Key Transfer — Name Collision Handling

## The Problem

When transferring a key to a target user who already has a key with the same name, the `unique_together = ('user', 'name')` constraint would fail.

## Solution: `base_name` Field + Counter Suffix

### Model

```python
class ApiKey(models.Model):
    name = models.CharField(max_length=255)        # Current display name
    base_name = models.CharField(max_length=255)   # Clean name, never has transfer suffixes
    transfer_count = models.PositiveIntegerField(default=0)
    # ... other fields ...
```

### On Creation
```python
key.name = name
key.base_name = name
```

### On Transfer
```python
def _generate_transfer_name(base_name, target_user):
    effective_base = base_name or 'key'  # Fallback for legacy keys
    
    new_name = effective_base
    counter = 1
    final_name = new_name
    while ApiKey.objects.filter(user=target_user, name=final_name).exists():
        final_name = f"{effective_base}-{counter}"
        counter += 1
    
    if len(final_name) > 255:
        base_truncated = effective_base[:255 - 4]
        final_name = base_truncated
        counter = 1
        while ApiKey.objects.filter(user=target_user, name=final_name).exists():
            final_name = f"{base_truncated}-{counter}"
            counter += 1
    
    return final_name[:255]
```

### Name Evolution

| Event | `name` | `base_name` | `transfer_count` |
|---|---|---|---|
| Create | `rack-key` | `rack-key` | 0 |
| 1st transfer | `rack-key` | `rack-key` | 1 |
| 2nd transfer | `rack-key` | `rack-key` | 2 |
| Collision (target has "rack-key") | `rack-key-1` | `rack-key` | 3 |
| 4th transfer to new user | `rack-key` | `rack-key` | 4 |
| Another collision | `rack-key-1` | `rack-key` | 5 |
| Collision again | `rack-key-2` | `rack-key` | 6 |

**Key insight:** `base_name` stays clean forever. The collision suffix (`-1`, `-2`, etc.) is only in the display `name`, never in `base_name`. On the next transfer, we start fresh from `base_name`, so old collision suffixes are automatically discarded.

### Why This Works

1. `base_name` is set once at creation and never changes
2. On each transfer, we use `base_name` (always clean) to generate the new name
3. If the target user already has a key with that name, we append `-1`, `-2`, etc.
4. The collision suffix is only in `name`, not in `base_name`
5. On the next transfer, `base_name` is still clean, so the old suffix is discarded

### Legacy Key Handling

For keys that existed before the `base_name` field was added (migration 0004), `base_name` is empty. The `_generate_transfer_name()` function falls back to `'key'` in this case. A data migration (0005) sets `base_name = name` for all existing keys.
