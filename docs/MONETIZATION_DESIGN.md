# Monetization System Design

## Executive Summary

Implement a usage-based billing system charging **$0.01 per rig per day** when a rig reports at least one heartbeat during that calendar day. This document compares multiple approaches and provides implementation recommendations.

---

## Pricing Model

| Plan | Cost Per Rig Per Day | Notes |\n|------|---------------------|-------|\n| Pay-as-you-go | $0.01 | No minimum commitment |\n| Monthly estimate | ~$30/rig/month | If online every day |\n| Minimum billing | 1 day | Charged even if rig online 1 second |

---

## Current System Analysis

### Rig Status Tracking (Already Exists)

**Rig model (`gpu_monitor/rigs/models.py`):**
- `last_seen`: DateTime - most recent heartbeat timestamp
- `status`: CharField with 'online'/'stale'/'offline' choices
- Status logic: Online (≤2min), Stale (>2min & ≤10min), Offline (>10min)

**RigStatusEvent model (`gpu_monitor/metrics_app/models.py`):**
- Tracks every status transition
- Fields: `rig_uuid`, `timestamp`, `status`, `previous_status`
- Enables uptime charts and downtime analysis

### Problem Statement

Current system tracks status changes but lacks:
1. **Daily aggregation** - Cannot query "how many rigs were online on 2026-07-01"
2. **Billing ledger** - No record of charges or balance tracking
3. **User-facing billing view** - No way for users to see costs

---

## Monetization Approaches (Industry Patterns)

### Approach 1: Heartbeat Count Method

Count all heartbeats per day, divide by minutes.

```
Per-day cost = (heartbeats / 1440) × $0.01 × rigs
```

**Pros:** Precise fractional billing
**Cons:** Expensive query, fractional cents require rounding logic

### Approach 2: Last Seen Date Window (Inaccurate)

Check if `last_seen >= start_of_day`.

**Pros:** Simple query
**Cons:** Misses rigs offline at midnight but online earlier

### Approach 3: Daily Active Rig Count (Recommended)

Create DailyRigUsage record on each ONLINE heartbeat.

```
IF rig.status = 'online' THEN create DailyRigUsage(date=today, rig=rig)
Billing = COUNT(DISTINCT rigs) × $0.01
```

**Pros:** Accurate per-day tracking, simple queries, auditable
**Cons:** Must handle duplicates, daily job required

### Approach 4: Incremental Per-Payload Billing (Proposed Analysis)

Charge incrementally on each heartbeat submission.

```
rate_per_minute = 0.01 / 1440  # $0.01 ÷ 1440 minutes/day
On each heartbeat: user.balance_cents -= rate_per_minute
```

#### Comparison Table

| Aspect | Daily Aggregation | Incremental Per-Payload |\n|--------|------------------|------------------------|\n| **Implementation** | Complex: 2 models + daily job | Simple: Just balance deduction on heartbeat |\n| **Database overhead** | One row per rig per day | No additional writes per heartbeat |\n| **Consistency** | Daily snapshot, fully auditable | Real-time, hard to audit |\n| **Race conditions** | None (daily batch) | Multiple concurrent heartbeats possible |\n| **Timezones** | Clear day boundaries | Ambiguous mid-day date changes |\n| **Partial day billing** | Full day if any online | Proportional (more fair) |\n| **Refund complexity** | Easy (delete/modify DailyRigUsage) | Hard (must track each tiny deduction) |\n| **Negative balance risk** | Controlled daily (can stop at 0) | Can go negative mid-day unexpectedly |\n| **Historical analysis** | Full audit trail (who, when, how much) | No rig-day history preserved |\n| **Lost data handling** | Still charge if any heartbeat | Under-billed if heartbeats missed |\n| **CPU overhead** | Daily batch job (1x/day) | Every heartbeat (1440x/day per rig) |\n| **Code complexity** | Moderate | Minimal (balance field only) |

---

## Recommended Approach: DailyRigUsage Model

### Why This Approach Wins

1. **Auditability**: Users can see exactly which days their rigs were billed
2. **Refunds**: Delete DailyRigUsage to reverse erroneous charges
3. **Race safe**: PostgreSQL unique constraints prevent duplicates
4. **Configurable**: Easy to change rates per day
5. **Scalable**: One write per rig per day, not 1440 writes

### Trade-offs

- Slightly more complex implementation
- But provides necessary transparency for billing

---

## Database Schema Changes

### 1. User Model Addition

```python
# File: gpu_monitor/accounts/models.py
class User(AbstractUser):
    email = models.EmailField(unique=True)
    is_admin = models.BooleanField(default=False)
    electricity_rate_kwh = models.DecimalField(
        max_digits=6, decimal_places=4, default=0.3300
    )
    
    # NEW FIELD - Track credit balance for billing
    balance_cents = models.PositiveIntegerField(
        default=0,
        help_text="User credit balance in cents (USD)"
    )
```

### 2. DailyRigUsage Model (NEW)

```python
# File: gpu_monitor/rigs/models.py
class DailyRigUsage(models.Model):
    """Tracks daily rig activity for billing purposes.
    
    One row per rig per day when rig was online at least once.
    Used for daily active rig counting and billing calculations.
    """
    rig = models.ForeignKey('rigs.Rig', on_delete=models.CASCADE)
    date = models.DateField(db_index=True)
    online_count = models.PositiveIntegerField(
        default=1,
        help_text="Number of times rig came online this day (typically 1)"
    )
    
    class Meta:
        db_table = 'rigs_daily_usage'
        verbose_name = 'Daily Rig Usage'
        verbose_name_plural = 'Daily Rig Usage'
        unique_together = ('rig', 'date')
        indexes = [
            models.Index(fields=['rig', '-date']),
            models.Index(fields=['date']),
        ]
    
    def __str__(self):
        return f"{self.rig.name} - {self.date}"
```

### 3. BillingRecord Model (NEW)

```python
# File: gpu_monitor/accounts/models.py
class BillingRecord(models.Model):
    """Monthly billing record for rig monitoring costs.
    
    Created once per month per user summarizing the cost of
    monitoring their rigs during that period.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='billing_records'
    )
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

## Edge Cases & Solutions

### Critical Edge Cases

| Edge Case | Solution |\n|-----------|----------|\n| Rig offline at midnight scan | Still billed if ONLINE heartbeat occurred |\n| Rig online at midnight boundary | UTC date of heartbeat used |\n| Multiple heartbeats same day | Unique constraint ensures one record per rig/day |\n| Rig transferred between users | New owner pays from transfer date |\n| Rig deleted mid-day | Still billed if DailyRigUsage exists |\n| Timezone handling | All dates in UTC, local only for display |\n| Concurrent heartbeats | PostgreSQL `get_or_create()` is atomic |\n| System clock drift | Server timestamp overrides rig timestamp |\n| Lost heartbeat data | Still charge if any ONLINE event recorded |\n| Refunds/credits | Delete/modify DailyRigUsage records |\n| Failed billing attempts | Retry logic in management command |\n| >1000 rigs per user | Indexed queries, tested at scale |

---

## Implementation Plan

### Phase 1: Data Collection Foundation

1. Create `DailyRigUsage` model with migration
2. Update `IngestView` to create DailyRigUsage on ONLINE status
3. Create `backfill_daily_usage.py` management command
4. Add index on `date` field for efficient daily queries

### Phase 2: Billing Logic

1. Create `BillingRecord` model with migration
2. Add `User.balance_cents` field with migration
3. Create `calculate_daily_billing.py` management command (runs at 02:00 UTC)
4. Handle negative balance scenarios (stop service vs. allow credit)

### Phase 3: Dashboard & API

1. Create API endpoint `/api/v1/billing/history/`
2. Create `BillingHistoryView` with monthly totals
3. Add dashboard tab "Billing" showing balance and history
4. Add Django URL routing

### Phase 4: Payment Processing (Future)

1. Add `stripe_customer_id` to User model
2. Create payment session endpoint
3. Add webhook handler for payment events
4. Add "Add Credit" button in dashboard

---

## Query Examples

### Daily Active Rigs Count

```python
from datetime import date, timedelta

def get_daily_active_rigs(user, target_date=None):
    """Count rigs that were online on the given date."""
    if target_date is None:
        target_date = date.today() - timedelta(days=1)
    
    return DailyRigUsage.objects.filter(
        rig__owner=user,
        date=target_date
    ).values('rig').distinct().count()
```

---

## Risk Assessment

| Risk | Impact | Mitigation |\n|------|--------|------------|\n| Duplicate DailyRigUsage | Data corruption | Unique constraint + get_or_create() |\n| Race conditions | Lost data | Atomic database operations |\n| Missing heartbeats | Under-billing | Still charge if any ONLINE event |\n| Negative balances | Revenue loss | Set minimum threshold, email alerts |\n| Timezone confusion | Wrong billing date | Always use UTC dates |\n| Performance | Slow queries | Proper indexing |

---

## Recommendation Summary

**Choose DailyRigUsage approach** because:
- Billing transparency is essential for user trust
- Refunds require audit trail
- Race conditions are manageable
- Storage cost is minimal (one row per rig per day)

The incremental approach may seem simpler but creates unauditable charges and makes refunds nearly impossible.