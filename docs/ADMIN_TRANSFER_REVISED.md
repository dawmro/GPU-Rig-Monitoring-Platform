# Admin Key Transfer — Revised Plan with Edge Cases

## Edge Cases and Problems

### Problem 1: Two-Step Form (Source User → Keys)

The plan describes a multi-step flow: first select source user, then show their keys. This requires either:
- **Option A:** JavaScript to dynamically load keys when source user changes
- **Option B:** Form submission to load keys (page reload)
- **Option C:** Show all keys from all users, filter by source user on submit

**Analysis:**
- Option A: More complex, requires JS
- Option B: Simple but requires page reload (poor UX)
- Option C: Simplest, but could be confusing with many keys

**Recommendation:** Option B with a "Load Keys" button. Simple, no JS, clear flow.

### Problem 2: Key Name Collision Across Users

When transferring Key X from User A to User B:
- User B might already have a key named "rack-key"
- `unique_together = ('user', 'name')` constraint would fail

**Current solution:** `_generate_transfer_name()` handles this by appending counter.

**But what if:**
- User B has "rack-key" (their own key)
- User B has "rack-key-1" (from previous transfer)
- We need to find the next available name

**Current implementation handles this:** The while loop checks `ApiKey.objects.filter(user=target_user, name=final_name).exists()` and increments counter.

### Problem 3: Transferring Keys with Enrolled Rigs

When transferring a key, ALL rigs enrolled by that key get transferred too (owner changes).

**Edge cases:**
- Key has 0 rigs: Transfer is harmless, just changes key ownership
- Key has 1 rig: Transfer key + rig
- Key has N rigs: Transfer key + ALL N rigs

**Problem:** What if admin only wants to transfer the key but NOT the rigs?
- Current design: Rigs always follow the key
- This is actually correct for the use case (transferring a rig to new owner means the key should go with it)

**But what if:** Admin wants to transfer a key to a different user but keep the rigs with the original user?
- This is a different use case: "re-keying" a rig
- Should be a separate operation: "Change rig's API key"
- Not part of key transfer

**Decision:** Rigs always follow the key. If admin wants to re-key, that's a separate feature.

### Problem 4: Revoked Keys

Should admin be able to transfer revoked keys?

**Analysis:**
- Yes: The key stays revoked, new owner can reactivate
- No: Revoked keys shouldn't be transferred (they're "dead")

**Decision:** Allow transfer of revoked keys. The key stays revoked. New owner sees it in their list and can reactivate if needed. This is consistent with the existing revoke/reactivate flow.

### Problem 5: Transfer Chain (A → B → C)

Admin transfers key from User A to User B. Then admin wants to transfer the same key from User B to User C.

**Current design:** This works because:
1. A → B: `key.user = B`, `key.name = base_name` (or `base_name-1` if collision)
2. B → C: `key.user = C`, `key.name = base_name` (collision suffix stripped, new one applied)

**But what if:** User C already has a key with the same base_name?
- `_generate_transfer_name()` handles this by appending counter
- Result: `base_name-1` (or `base_name-2`, etc.)

**This is correct behavior.**

### Problem 6: Concurrent Transfers

What if two admins try to transfer the same key simultaneously?

**Analysis:**
- Django's default isolation level (READ COMMITTED) prevents lost updates
- The second transfer would fail because the key's `user` has already changed
- The `user=request.user` filter in the view would reject it

**But wait:** The admin view doesn't filter by `user=request.user` — it filters by source user selected in the form. So two admins COULD try to transfer the same key.

**Mitigation:** Add a check at the start of the transfer:
```python
# Re-fetch key and verify it still belongs to source user
key = get_object_or_404(ApiKey, id=key_id, user=source_user)
```

This ensures the key hasn't been transferred by another admin between page load and form submission.

### Problem 7: Large Number of Keys

What if a user has 100+ keys? The checkbox list would be very long.

**Mitigation:**
- Add pagination or search
- For now, keep it simple (show all keys)
- Can add search/filter later if needed

### Problem 8: Transferring Keys That Don't Belong to Source User

What if admin selects User A as source, but manually crafts a POST with User B's key ID?

**Current design:** The view filters keys by source user:
```python
keys = ApiKey.objects.filter(id__in=key_ids, user=source_user)
```

This correctly rejects keys that don't belong to the source user. **This is secure.**

### Problem 9: Empty Key Selection

What if admin clicks "Transfer" without selecting any keys?

**Current design:** The view checks `if not key_ids` and rejects. **This is handled.**

### Problem 10: Source User = Target User

What if admin selects the same user as both source and target?

**Current design:** The view checks `if source_user == target_user` and rejects. **This is handled.**

### Problem 11: Non-Existent Target User

What if admin crafts a POST with a non-existent user ID?

**Current design:** `get_object_or_404(User, id=target_user_id)` returns 404. **This is handled.**

### Problem 12: Key Name Length After Multiple Transfers

With `base_name` staying clean and collision suffix being stripped each time, names should never grow unbounded.

**Example:**
- Create: `rack-key` (base_name: `rack-key`)
- Transfer to B: `rack-key` (base_name: `rack-key`)
- B has `rack-key` already: `rack-key-1` (base_name: `rack-key`)
- Transfer to C: `rack-key` (base_name: `rack-key`, old `-1` stripped)
- C has `rack-key` and `rack-key-1`: `rack-key-2` (base_name: `rack-key`)

**This is correct.** Names never grow beyond `base_name-N`.

### Problem 13: What Happens to Rig Tags After Transfer?

When a rig is transferred to a new user, it keeps its old tags. But those tags belong to the OLD user.

**Problem:** The new user won't see those tags in their tag list (tags are per-user).

**Options:**
1. Clear tags on transfer (simplest)
2. Duplicate tags for new user (complex)
3. Leave tags as-is (confusing for new user)

**Decision:** Clear tags on transfer. The new owner can re-tag the rig.

```python
# In transfer view, after updating rig ownership:
for rig in Rig.objects.filter(enrolled_by_api_key=key):
    rig.tags.clear()
```

### Problem 14: Audit Trail

Who transferred what? When? From whom to whom?

**Current design:** Uses `log_audit_event()` which stores:
- `action`: 'apikey.transferred'
- `target_type`: 'ApiKey'
- `target_id`: key.id
- `metadata`: {from_user, to_user, rig_count}

**But:** The audit middleware stores this on `request._audit_event`. We need to make sure it gets persisted.

**Check:** The `AuditMiddleware` should handle this. Let me verify the middleware persists the event.

## Revised Implementation Plan

### Step 1: Revert User-Facing Transfer UI
- Remove transfer form, checkboxes, dropdown, button from `api_keys.html`
- Remove `transfer_api_keys` view from `accounts/views.py`
- Remove `transfer-api-keys` URL from `accounts/urls.py`
- Remove `all_users` from `api_keys()` view context

### Step 2: Add Admin Transfer View
- New view: `admin_transfer_keys()` in `accounts/views.py`
- Staff-only access check
- Two-step form: select source user → select keys → select target → confirm
- Handles all edge cases listed above

### Step 3: Add Admin Transfer Template
- `templates/accounts/admin_transfer_keys.html`
- Source user dropdown
- Key selection (checkboxes with rig count and enrolled rigs)
- Target user dropdown
- Warning message showing what will be transferred
- Confirmation button

### Step 4: Add URL
- `path('accounts/admin/transfer-keys/', views.admin_transfer_keys, name='admin-transfer-keys')`

### Step 5: Add Navigation Link
- In `base.html`, add "Transfer Keys" link visible only to `is_staff` users

### Step 6: Handle Rig Tags
- Clear rig tags on transfer (tags are per-user)

### Step 7: Add Concurrency Protection
- Re-fetch key and verify it still belongs to source user before transfer
