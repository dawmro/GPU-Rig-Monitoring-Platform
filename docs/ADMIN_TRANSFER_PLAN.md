# Admin Key Transfer — Problem Analysis and Plan

## Current Problem

### What's broken:
1. The `transfer_api_keys` view uses `request.user` as the source: `ApiKey.objects.filter(id__in=key_ids, user=request.user)`
2. This means admin can only transfer THEIR OWN keys
3. After admin transfers a key, it belongs to another user — nobody else can transfer it
4. There's no way for admin to transfer keys on behalf of other users

### User Story That Fails:
1. Admin wants to transfer User A's key to User B
2. Admin goes to API keys page — sees only admin's own keys
3. Admin CANNOT see User A's key to transfer it
4. Even if admin could see it, the filter `user=request.user` would reject it

## Recommended Solution: Admin Transfer Panel

### Design: Separate Admin Menu Item

Add a new menu item "Transfer Keys" visible only to staff users. This opens a dedicated page where admin can:
1. Select SOURCE user (whose keys to transfer)
2. See all keys belonging to that user
3. Select KEY(S) to transfer
4. Select TARGET user
5. Confirm transfer

### Why This Works:
- Admin sees ALL users' keys (not just their own)
- Clear separation: source user → target user
- No confusion about whose keys are being transferred
- Full audit trail

### URL Structure:
```
/accounts/admin/transfer-keys/
```

### Access Control:
- Only `is_staff` users can access
- Regular users get 403 or redirect

### UI Layout:

```
┌─────────────────────────────────────────────────────────┐
│ Transfer API Keys                                        │
│                                                          │
│ Step 1: Select Source User                               │
│ [Dropdown: all users]                                    │
│                                                          │
│ Step 2: Select Key(s)                                    │
│ ☐ Key "rack-key" (Active, 3 rigs) — User: a@test.com    │
│ ☐ Key "farm-key" (Active, 1 rig) — User: a@test.com     │
│                                                          │
│ Step 3: Select Target User                               │
│ [Dropdown: all users except source]                      │
│                                                          │
│ [Transfer Selected Keys]                                 │
│                                                          │
│ Warning: This will transfer 2 key(s) and 4 rig(s) from   │
│ a@test.com to b@test.com. This action cannot be undone.  │
└─────────────────────────────────────────────────────────┘
```

### Implementation Plan

1. **Revert user-facing transfer UI** from `api_keys.html` (remove checkboxes, dropdown, button)
2. **Remove `transfer_api_keys` view** from `accounts/views.py`
3. **Remove transfer URL** from `accounts/urls.py`
4. **Remove `all_users` context** from `api_keys()` view
5. **Keep `_generate_transfer_name()`** helper (admin view will use it)
6. **Add new admin view** `admin_transfer_keys()`
7. **Add new admin template** for the transfer UI
8. **Add URL** for admin transfer page
9. **Add navigation link** visible only to staff

### Files to Modify:

1. `accounts/views.py`:
   - Remove `transfer_api_keys()` view
   - Add `admin_transfer_keys()` view (staff-only)
   - Add `_generate_transfer_name()` helper
   - Remove `all_users` from `api_keys()` context

2. `accounts/urls.py`:
   - Remove `transfer-api-keys` URL
   - Add `admin/transfer-keys` URL

3. `templates/accounts/api_keys.html`:
   - Remove all transfer UI (checkboxes, dropdown, button, JavaScript)
   - Keep revoke/reactivate/delete buttons only

4. `templates/accounts/admin_transfer_keys.html` (new):
   - Source user dropdown
   - Key selection (checkboxes)
   - Target user dropdown
   - Warning message showing what will be transferred
   - Transfer button

5. `templates/base.html` or navigation:
   - Add "Transfer Keys" nav link for staff users

### Security Considerations:

1. **Staff-only access**: `@login_required` + `request.user.is_staff` check
2. **Cannot transfer to self**: Validate source ≠ target
3. **Preview before commit**: Show what will be transferred
4. **Audit logging**: Log who transferred what to whom
5. **CSRF protection**: Standard Django CSRF
6. **Confirmation step**: Show warning before executing

### Edge Cases:

| Case | Handling |
|---|---|
| Source = Target | Reject with error |
| No keys selected | Reject with error |
| Key has 0 rigs | Allow transfer (no rigs affected) |
| Key has N rigs | Transfer key + all N rigs with warning |
| Target has same key name | Auto-append counter via _generate_transfer_name |
| Source user has no keys | Show info message |
| Non-staff access | 403 Forbidden |
