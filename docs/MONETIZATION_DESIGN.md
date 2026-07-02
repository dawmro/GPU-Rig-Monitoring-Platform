# Monetization System Design

## Executive Summary

Implement a usage-based billing system charging **$0.01 per rig per day** when a rig reports at least one heartbeat during that calendar day.

---

## Pricing Model

| Plan | Cost Per Rig Per Day | Monthly Estimate |\n|------|---------------------|-----------------|\n| Pay-as-you-go | $0.01 | ~$30 if online daily |

---

## Current System Analysis

**Rig model** (`gpu_monitor/rigs/models.py`):
- `last_seen`: DateTime - heartbeat timestamp
- `status`: 'online'/'stale'/'offline' (computed from last_seen)

**RigStatusEvent model** (`gpu_monitor/metrics_app/models.py`):
- Tracks every status transition
- Enables uptime charts

### Missing Components
1. Daily aggregation for billing
2. Balance tracking
3. User-facing billing history

---

## Monetization Approach: DailyRigUsage (CHOSEN)

### Why This Approach

| Benefit | Details |\n|---------|---------|\n| Auditability | Users see exactly which days their rigs were billed |\n| Refunds | Delete DailyRigUsage to reverse erroneous charges |\n| Race safe | PostgreSQL unique constraints prevent duplicates |\n| Simple | One write per rig per day |\n| Storage | Minimal overhead |

**Logic:**
```
ON heartbeat WITH status='online':
    CREATE DailyRigUsage(rig=X, date=today) IF NOT EXISTS

Daily billing job:
    FOR EACH user:
        rigs_online = COUNT(DailyRigUsage WHERE date=yesterday AND rig.owner=user)
        charge = rigs_online × $0.01
        CREATE BillingRecord(user, amount=-charge)
        UPDATE user.balance_cents = balance_cents - charge
```

---

## Database Schema Changes

### 1. User Model

```python
# gpu_monitor/accounts/models.py
class User(AbstractUser):
    # ... existing fields ...
    
    # NEW FIELD - Credit balance in cents
    balance_cents = models.PositiveIntegerField(
        default=0,
        help_text="User credit balance in cents (USD)"
    )
```

### 2. DailyRigUsage Model (NEW)

```python
# gpu_monitor/rigs/models.py
class DailyRigUsage(models.Model):
    """One row per rig per day when online."""
    rig = models.ForeignKey('rigs.Rig', on_delete=models.CASCADE, related_name='daily_usage')
    date = models.DateField(db_index=True)
    
    class Meta:
        db_table = 'rigs_daily_usage'
        unique_together = ('rig', 'date')
        indexes = [
            models.Index(fields=['rig', '-date']),
            models.Index(fields=['date']),
        ]
```

### 3. BillingRecord Model (NEW)

```python
# gpu_monitor/accounts/models.py
class BillingRecord(models.Model):
    """Monthly billing ledger entries."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='billing_records')
    period_start = models.DateField()
    period_end = models.DateField()
    rig_days_used = models.PositiveIntegerField()
    amount_cents = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'accounts_billing_record'
        ordering = ['-period_start']
        unique_together = ('user', 'period_start')
```

---

## Balance Field Type: PositiveIntegerField (CHOSEN)

### Comparison

| Type | Pros | Cons |\n|------|------|------|\n| PositiveIntegerField | Exact precision, fast, industry standard | Convert to dollars for display |\n| DecimalField | Native dollars | Slower arithmetic, complex F() |\n| FloatField | Simple | `0.1 + 0.2 = 0.30000000000000004` - NEVER USE |

**Decision: PositiveIntegerField for cents**

- PostgreSQL max: ~2 billion cents = ~$21 million
- Sufficient for any realistic balance

---

## Implementation Phases

### Phase 1: Models
- Add `balance_cents` to User model
- Create `DailyRigUsage` model
- Create `BillingRecord` model
- Run migrations

### Phase 2: Ingest Integration
**File: `gpu_monitor/metrics_app/views.py`**
```python
# After rig.save() in IngestView.post():
if rig.status == Rig.Status.ONLINE:
    DailyRigUsage.objects.get_or_create(
        rig=rig, 
        date=timezone.now().date()
    )
```

### Phase 3: Billing Command
**File: `gpu_monitor/management/commands/calculate_billing.py`**
- Run daily at 02:00 UTC
- Aggregate yesterday's DailyRigUsage
- Create BillingRecords
- Deduplicate with F() expressions

### Phase 4: Dashboard API
**File: `gpu_monitor/dashboard/views.py`**
```python
class BillingHistoryView(APIView):
    def get(self, request):
        # Return balance, month-to-date usage, billing history
```

### Phase 5: Cron Scheduling
```
0 2 * * * /opt/monitoring-platform/manage.py calculate_billing
```

---

## Edge Cases Handled

| Edge Case | Solution |\n|-----------|----------|\n| Rig offline at midnight | Billed if ONLINE heartbeat occurred |\n| Midnight boundary | UTC date used |\n| Multiple heartbeats/day | Unique constraint prevents duplicates |\n| Rig transfer | New owner pays from transfer date |\n| Rig deletion | Still billed if DailyRigUsage exists |\n| Timezone handling | All dates in UTC |\n| Concurrent heartbeats | `get_or_create()` is atomic |\n| System clock drift | Server timestamp overrides |\n| Lost heartbeats | Still charge if any ONLINE event |\n| Refunds | Delete DailyRigUsage |\n| >1000 rigs | Indexed queries |

---

## Risk Assessment

| Risk | Mitigation |\n|------|------------|\n| Duplicate DailyRigUsage | Unique constraint + get_or_create() |\n| Race conditions | Atomic database operations |\n| Negative balances | Email alert when balance low |\n| Timezone issues | Always use UTC dates |\n| Performance | Index on date field |