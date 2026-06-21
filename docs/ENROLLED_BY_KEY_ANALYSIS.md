# enrolled_by_api_key Update on Ingest — Edge Case Analysis

## The Fix (Current Implementation)

```python
# In IngestView, after ownership check:
if rig.enrolled_by_api_key_id != api_key.id:
    rig.enrolled_by_api_key = api_key
    rig.save(update_fields=['enrolled_by_api_key'])
```

## Pros

1. **Seamless key rotation**: Agent changes key → next payload automatically updates the link. No manual intervention needed.
2. **No downtime**: Rig keeps reporting during key transition. No need to delete and re-enroll.
3. **Simple**: Minimal code change, easy to understand.
4. **Backward compatible**: Existing behavior unchanged for keys that don't rotate.

## Cons and Edge Cases

### Edge Case 1: Legitimate Multi-Key Usage (One Rig, Multiple Keys)

**Scenario:** User has one rig but two API keys (Key A and Key B). The rig was enrolled with Key A. User configures the agent to use Key B.

**Current fix behavior:** `enrolled_by_api_key` changes from Key A to Key B. Key A now shows 0 rigs, Key B shows 1 rig.

**Is this correct?** 
- If the user intentionally rotated the key → YES, correct.
- If the user has TWO different agents on the same rig using different keys → NO, problematic. The last key to send a payload "wins" the rig.

**But wait:** Can two different agents on the same rig use different keys? The agent identifies the rig by UUID. If two agents send the same UUID with different keys, they'd conflict on ownership (different users) or flip-flop the `enrolled_by_api_key` (same user).

**Verdict:** This is an unlikely edge case. If a user has one rig, they should use one key. If they want to rotate, the fix handles it correctly.

### Edge Case 2: Key Transfer Followed by Agent Key Change

**Scenario:**
1. Admin transfers Key A (with Rig X) from User 1 to User 2
2. Agent on Rig X still has Key A configured → keeps sending with Key A
3. `enrolled_by_api_key` is Key A → Rig X shows under Key A for User 2 ✓
4. User 2 creates Key B and updates the agent
5. Agent sends with Key B → `enrolled_by_api_key` updates to Key B ✓

**Verdict:** Works correctly.

### Edge Case 3: Key Revocation During Rotation

**Scenario:**
1. User revokes Key A
2. Agent still has Key A → gets 401 Unauthorized
3. User creates Key B, updates agent
4. Agent sends with Key B → `enrolled_by_api_key` updates to Key B ✓

**Verdict:** Works correctly. The 401 forces the agent to stop using the revoked key.

### Edge Case 4: Accidental Key Reuse Across Users

**Scenario:** User A configures their agent with User B's API key (mistake).

**Current behavior:**
1. Agent sends with User B's key
2. Rig exists, owned by User A
3. Ownership check: `rig.owner_id (A) != user.id (B)` → 409 error
4. `enrolled_by_api_key` is NOT updated (ownership check fails first)

**Verdict:** Correct. The ownership check prevents cross-user key misuse.

### Edge Case 5: Rapid Key Switching

**Scenario:** User rapidly changes the agent's key back and forth between Key A and Key B.

**Behavior:** `enrolled_by_api_key` flips on each payload. The last key to send "wins".

**Is this a problem?** No. It's expected behavior. The rig is linked to whatever key was used last.

### Edge Case 6: Database Write on Every Ingest

**Concern:** The fix adds a potential `rig.save()` on EVERY ingest payload (if key changed). This is an extra DB write.

**Mitigation:** 
- The `if` check ensures it only writes when the key actually changed
- Key rotation is rare (not on every payload)
- The write is a single field update (`update_fields=['enrolled_by_api_key']`)
- Ingest already does `rig.save(update_fields=['last_seen', 'status'])` — we could combine these

**Optimization opportunity:** Combine the two saves into one:
```python
update_fields = ['last_seen', 'status']
if rig.enrolled_by_api_key_id != api_key.id:
    rig.enrolled_by_api_key = api_key
    update_fields.append('enrolled_by_api_key')
rig.save(update_fields=update_fields)
```

### Edge Case 7: Race Condition (Concurrent Ingests with Different Keys)

**Scenario:** Two agents send payloads for the same rig simultaneously with different keys.

**Behavior:** 
- Both pass ownership check (same user)
- Both update `enrolled_by_api_key`
- Last write wins (standard Django ORM behavior)

**Is this a problem?** Extremely unlikely. Would require two agents on the same rig with different keys sending at the exact same millisecond. Even then, the result is just which key is linked — no data loss.

### Edge Case 8: Key Deleted After Agent Configured

**Scenario:** User deletes Key A from the dashboard but agent still has Key A configured.

**Behavior:**
1. Agent sends with Key A
2. `ApiKey.validate_key()` fails (key is inactive/deleted) → 401
3. IngestView never reached → `enrolled_by_api_key` not updated

**Verdict:** Correct. The agent must be reconfigured with a valid key.

## Revised Implementation

Based on the analysis, the fix is correct but can be optimized:

```python
# After ownership check in IngestView:
# Update enrolled_by_api_key to current key (handles key rotation)
update_fields = ['last_seen', 'status']
if rig.enrolled_by_api_key_id != api_key.id:
    rig.enrolled_by_api_key = api_key
    update_fields.append('enrolled_by_api_key')
rig.save(update_fields=update_fields)
```

This combines the two DB writes into one, reducing the performance impact.

## Summary

| Edge Case | Behavior | Correct? |
|---|---|---|
| Key rotation (same user) | enrolled_by_api_key updates to new key | ✅ Yes |
| Multi-key on same rig | Last key wins | ✅ Acceptable |
| Key transfer + rotation | Works correctly | ✅ Yes |
| Revoked key | 401 prevents ingest, no update | ✅ Yes |
| Cross-user key misuse | 409 prevents update | ✅ Yes |
| Rapid key switching | Last key wins | ✅ Acceptable |
| DB write overhead | Combined into single save | ✅ Optimized |
| Race condition | Last write wins | ✅ Acceptable |

**Conclusion:** The fix is safe and correct for all realistic edge cases. The only optimization needed is combining the two `rig.save()` calls into one.
