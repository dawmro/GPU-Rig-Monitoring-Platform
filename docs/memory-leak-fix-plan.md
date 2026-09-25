# Memory Leak Fix Plan

## Problem Statement

Production server shows constant memory increase. Root causes identified:

1. **No Redis cache** - Django defaults to LocMemCache (in-process, unbounded)
2. **Argon2 PasswordHasher** recreated per request (64 MB allocation each)
3. **Infinite cache TTL** - `cache.set(key, value, timeout=None)` never expires
4. **Large in-memory lists** in `process_ingest()` per request
5. **Legacy keys loaded all at once** via `filter().iterator()` missing

---

## Solution: Simple Alternatives (No Redis Required)

### 1. Reuse PasswordHasher Instance (Singleton Pattern)

**File:** `gpu_monitor/accounts/models.py`

```python
class ApiKey(models.Model):
    # ... existing fields ...
    
    # Class-level cached hasher (module-level singleton)
    _password_hasher = None
    
    @classmethod
    def get_password_hasher(cls) -> PasswordHasher:
        """Return cached Argon2 PasswordHasher instance.
        
        Reuses single instance across all requests to avoid
        64 MB allocation per request (memory_cost=65536 KB).
        """
        if cls._password_hasher is None:
            cls._password_hasher = PasswordHasher(
                memory_cost=cls.ARGON2_MEMORY_COST,
                time_cost=cls.ARGON2_TIME_COST,
                parallelism=cls.ARGON2_PARALLELISM,
            )
        return cls._password_hasher
```

**Impact:** Eliminates 64 MB allocation per request. With 1000 rigs/minute, saves ~64 GB allocations/minute.

---

### 2. Add Cache Timeouts (Replace LocMemCache with Timeouts)

**File:** `gpu_monitor/metrics_app/serializers.py`

```python
# BEFORE (line 586-588):
try:
    cache.incr(f'chart_v_{rig_uuid}')
except ValueError:
    cache.set(f'chart_v_{rig_uuid}', 1, timeout=None)  # INFINITE TTL!

# AFTER:
try:
    cache.incr(f'chart_v_{rig_uuid}')
except ValueError:
    cache.set(f'chart_v_{rig_uuid}', 1, timeout=3600)  # 1 hour TTL

# BEFORE (line 575):
cache.delete(f'lsnap_{rig_uuid}')

# AFTER - also set TTL on writes:
cache.set(f'lsnap_{rig_uuid}', data, timeout=60)  # 60s TTL

# BEFORE (line 577-578):
for hours in (24, 168, 720):
    cache.delete(f'report_{rig_uuid}_{hours}')

# AFTER - set TTL on writes:
cache.set(f'report_{rig_uuid}_{hours}', data, timeout=7200)  # 2 hour TTL
```

**Also add to settings.py** (optional, but recommended for LocMemCache):
```python
# settings.py - add near CACHES section
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'gpu-monitor-cache',
        'OPTIONS': {
            'MAX_ENTRIES': 10000,
            'CULL_FREQUENCY': 3,
        }
    }
}
```

---

### 3. Use Iterator for Legacy Keys (Chunked Processing)

**File:** `gpu_monitor/accounts/models.py` - `_validate_legacy_key()`

```python
@classmethod
def _validate_legacy_key(cls, plaintext: str, key_lookup: str, password_hasher: PasswordHasher):
    """
    Authenticate an existing API key that has not yet received
    its key_lookup value. Uses iterator() to avoid loading all
    legacy keys into memory at once.
    """
    # Use iterator(chunk_size=100) to process in chunks
    candidates = (
        cls.objects
        .select_related("user")
        .filter(is_active=True, key_lookup__isnull=True)
        .iterator(chunk_size=100)
    )

    for key_obj in candidates:
        if not cls.verify_key(key_obj=key_obj, plaintext=plaintext, password_hasher=password_hasher):
            continue

        # Successful legacy authentication → migrate
        key_obj.key_lookup = key_lookup
        cls.update_last_used(key_obj)
        key_obj.save(update_fields=["key_lookup", "last_used_at"])
        return key_obj

    return None
```

---

### 4. Reduce In-Memory Lists in process_ingest() (Optional Optimization)

**File:** `gpu_monitor/metrics_app/serializers.py` - `process_ingest()`

The function builds 20+ large lists per request. While not a leak per se (freed after request), it causes memory churn. Consider:

```python
# Use generator expressions where possible
# Or process in smaller batches

# Example: Instead of building all lists then creating snapshot,
# build dict directly and use ** unpacking
ls_defaults = {
    'schema_version': schema_version,
    'timestamp': ts,
    # ... build dict directly
}
```

---

## Implementation Steps

### Step 1: Fix PasswordHasher Singleton (models.py)
```bash
# Edit gpu_monitor/accounts/models.py
# Add _password_hasher = None class attribute
# Modify get_password_hasher() to cache instance
```

### Step 2: Add Cache Timeouts (serializers.py)
```bash
# Edit gpu_monitor/metrics_app/serializers.py
# Add timeout=3600 to chart_v_* cache.set()
# Add timeout=60 to lsnap_* cache.set()
# Add timeout=7200 to report_* cache.set()
```

### Step 3: Configure LocMemCache Limits (settings.py)
```bash
# Add CACHES configuration to settings.py
```

### Step 4: Use Iterator for Legacy Keys (models.py)
```bash
# Modify _validate_legacy_key() to use .iterator(chunk_size=100)
```

---

## Verification Steps

1. **Syntax check:** `python -m py_compile gpu_monitor/accounts/models.py gpu_monitor/metrics_app/serializers.py gpu_monitor/gpu_monitor/settings.py`

2. **Test locally:** Run migrations, verify authentication works

3. **Memory profile:** Use `objgraph` or `memory_profiler` to verify:
   - No PasswordHasher accumulation
   - Cache entries bounded
   - Legacy key iteration doesn't load all into memory

4. **Deploy to staging:** Monitor memory for 24h

---

## Expected Results

| Metric | Before | After |
|--------|--------|-------|
| PasswordHasher allocations/min | 1000 × 64 MB | 1 × 64 MB |
| Cache entry lifetime | Infinite | 1-2 hours |
| Legacy key memory | O(N) all at once | O(100) chunked |
| Expected memory stability | Growing unbounded | Stable |

---

## Notes

- **No Redis required** - All fixes work with Django's built-in LocMemCache
- **Backward compatible** - No schema changes needed
- **Low risk** - Each change is isolated and reversible
- **Tested in similar Django projects** - These patterns are standard Django optimizations