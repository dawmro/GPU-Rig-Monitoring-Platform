# API Key Transfer — Detailed Analysis

## User Stories

### Story 1: Transfer single rig to another user
- User A has 1 rig enrolled by Key X
- User A wants to transfer the rig to User B
- After transfer: User B owns the rig, agent keeps working with Key X
- **Key X must be transferred to User B** (otherwise User A could revoke it)

### Story 2: Transfer entire rack (multiple rigs) to another user
- User A has 5 rigs all enrolled by Key X (one key per rig, but all keys owned by User A)
- User A wants to transfer all 5 rigs to User B
- After transfer: User B owns all 5 rigs, all agents keep working
- **All 5 keys must be transferred to User B**

### Story 3: Transfer subset of rigs
- User A has 5 rigs: rig1-3 enrolled by Key X, rig4-5 enrolled by Key Y
- User A wants to transfer only rig1-3 to User B
- After transfer: User B owns rig1-3, User A keeps rig4-5
- **Only Key X needs to be transferred**

### Story 4: Transfer rig, then later re-key
- User A transfers rig to User B (key transferred)
- Later, User B wants to use their own key for the rig
- User B creates Key Z, updates agent config on the rig
- **This is a separate operation from transfer**

---

## Approach 3: Transfer API Key Ownership — Deep Analysis

### How it works:
1. Admin selects API key(s) to transfer
2. Admin selects target user
3. `key.user = new_user` for each selected key
4. All rigs enrolled by those keys now belong to the new user (implicitly, via the key)

### Edge Cases and Problems:

#### Problem 1: Rig ownership becomes inconsistent
- After transferring Key X from User A to User B:
  - `key.user = User B`
  - `rig.owner = User A` (unchanged!)
  - `rig.enrolled_by_api_key = Key X` (unchanged, but Key X now belongs to User B)
- **Result:** The rig's `.owner` still points to User A, but the key belongs to User B
- **Impact:** User A still "owns" the rig in the DB, User B owns the key
- **Fix needed:** Also update `rig.owner = new_user` when transferring the key

#### Problem 2: Old owner still has the key value
- User A created Key X and configured it on the agent
- User A might have stored the plaintext key somewhere
- After transfer, User A could still use the key to authenticate
- **Impact:** Security risk — User A could still send data as if they own the rig
- **Mitigation:** Generate a new key hash on transfer (re-keying), but this breaks the agent

#### Problem 3: All rigs enrolled by the key get transferred
- If Key X is used by 3 rigs, transferring Key X transfers ALL 3 rigs
- User A might only want to transfer 1 of the 3 rigs
- **Impact:** No granular control
- **Mitigation:** Only allow transfer if key has ≤ 1 rig, OR transfer all rigs with clear warning

#### Problem 4: What if the key is used by 0 rigs?
- Transferring a key with no rigs is pointless but not harmful
- **Impact:** Low — just a no-op

#### Problem 5: What if the target user already has a key with the same name?
- `unique_together = ('user', 'name')` constraint
- If User B already has a key named "rack-key", transfer fails
- **Impact:** Need to rename the key during transfer, or reject with error

#### Problem 6: Audit trail
- Who transferred the key? When?
- **Impact:** Need to log the transfer event for accountability

#### Problem 7: Revoked keys
- Should we allow transferring revoked keys?
- If yes: the key stays revoked, new owner must reactivate
- If no: reject transfer of revoked keys
- **Impact:** Decision needed on business logic

---

## Approach 5: Key-per-rig + Transfer — Deep Analysis

### How it works:
1. Enforce `unique=True` on `enrolled_by_api_key` (one key per rig max)
2. To transfer: transfer the API key ownership
3. Agent keeps using the same key

### Edge Cases and Problems:

#### Problem 1: Users who want one key for multiple rigs
- Some users manage a rack of servers with one key
- They WANT one key to enroll multiple rigs
- **Impact:** Forcing unique key per rig breaks this use case
- **User's own words:** "some users might actually want to transfer all rigs connected to one api key"

#### Problem 2: Same security issues as Approach 3
- Old owner still has the key value
- Need to also update rig.owner

#### Problem 3: Migration complexity
- Existing data might have multiple rigs per key
- Migration would fail unless we first deduplicate
- **Impact:** Complex migration, potential data loss

---

## Recommended Approach: Flexible Key Transfer (No Unique Constraint)

### Design:
1. **Do NOT enforce unique key per rig** — allow multiple rigs per key
2. **Transfer API key ownership** — change `key.user` to new user
3. **Also update rig.owner** for all rigs enrolled by the key
4. **Handle edge cases:**
   - Rename key if target user has same name
   - Log audit event
   - Allow transferring revoked keys (they stay revoked)
   - Clear warning showing how many rigs will be transferred

### User Flow:
1. Admin goes to API keys page
2. Selects one or more keys to transfer
3. Selects target user from dropdown
4. System shows warning: "This will transfer Key X and 3 enrolled rigs to User B"
5. Admin confirms
6. System:
   - Updates `key.user = new_user` for each key
   - Updates `rig.owner = new_user` for each enrolled rig
   - Logs audit event
   - Shows success message

### Edge Case Handling:

| Edge Case | Handling |
|---|---|
| Key has 0 rigs | Allow transfer (no rigs affected) |
| Key has 1 rig | Transfer key + rig |
| Key has N rigs | Transfer key + all N rigs with warning |
| Target user has same key name | Auto-append "-transfer" or reject |
| Key is revoked | Allow transfer (stays revoked) |
| Key is active | Allow transfer (stays active) |
| Old owner has key value | Out of scope — old owner should delete their copy |

### Security Considerations:
- **Old owner still has key value:** This is inherent to the design. The old owner configured the agent with the key. They could still use it until the agent is reconfigured. This is acceptable because:
  - The transfer is typically between trusted parties (e.g., selling a rig)
  - The new owner can re-key the rig at any time
  - The old owner loses access to the dashboard for that rig
- **Re-keying:** New owner can create a new key and update the agent config later. This is a separate operation.

### Benefits:
- ✅ No agent reconfiguration needed at transfer time
- ✅ Supports transferring single rig, multiple rigs, or entire rack
- ✅ Flexible — users can use one key per rig OR one key for many rigs
- ✅ Simple admin UI — just select key(s) and target user
- ✅ Clear audit trail

### Risks:
- ⚠️ Old owner still has key value (mitigated by re-keying later)
- ⚠️ No granular control — all rigs enrolled by the key get transferred (this is actually a FEATURE for rack transfers)
