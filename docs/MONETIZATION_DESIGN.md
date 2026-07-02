# Monetization System Design

## Executive Summary

Implement a usage-based billing system charging **$0.01 per rig per day** when a rig reports at least one heartbeat during that calendar day. This document outlines the architecture, database changes, edge cases, and implementation phases.

---

## Pricing Model

| Plan | Cost Per Rig Per Day | Notes |\n|------|---------------------|-------|\n| Pay-as-you-go | $0.01 | No minimum commitment |\n| Monthly estimate | ~$30/rig/month | If online every day |\n| Minimum billing | 1 day | Charged even if rig online 1 second |

---

## Current System Analysis

### Rig Status Tracking (Already Exists)

**Rig model (`gpu_monitor/rigs/models.py`, lines 20-80):**
- `last_seen`: DateTime - most recent heartbeat timestamp
- `status`: CharField with 'online'/'stale'/'offline' choices
- Status logic: Online (≤2min), Stale (>2min & ≤10min), Offline (>10min)

**RigStatusEvent model (`gpu_monitor/metrics_app/models.py`, lines 309-329):**
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

**Pros:** Accurate per-day tracking, simple queries
**Cons:** Must handle duplicates

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
            models.Index(fields=['date']),  # Critical for daily aggregation
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
    period_start = models.DateField()  # Month start: 2026-07-01
    period_end = models.DateField()    # Month end: 2026-07-31
    rig_days_used = models.PositiveIntegerField()  # Total rig-days
    amount_cents = models.IntegerField()  # USD cents (negative = charge)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'accounts_billing_record'
        ordering = ['-period_start']
        unique_together = ('user', 'period_start')
    
    @property
    def amount_usd(self):
        return self.amount_cents / 100.0
```

---

## Edge Cases & Solutions

### Critical Edge Cases

| Edge Case | Solution |\n|-----------|----------|\n| Rig offline at midnight scan | Still billed if ONLINE heartbeat occurred (DailyRigUsage exists) |\n| Rig online at midnight boundary | Date determined by UTC date of heartbeat, not local |\n| Multiple heartbeats same day | Unique constraint ensures one DailyRigUsage record per rig/day |\n| Rig transferred between users | New owner pays from transfer date onward (update owner_id on rig) |\n| Rig deleted mid-day | Still billed if DailyRigUsage exists for that day |\n| User deleted | Cascade delete preserves billing history (change to SET_NULL?) |\n| Timezone handling | All dates in UTC. User timezone only for display. |\n| Concurrent heartbeat writes | `get_or_create()` is atomic in PostgreSQL |\n| System clock drift | Server timestamp overrides rig timestamp |\n| Lost heartbeat data | Still billed if at least one ONLINE event recorded |\n| Refunds/credits | CreditAdjustment model with positive amounts |\n| Failed billing attempts | Retry logic in management command, admin notification |\n| >1000 rigs per user | Indexed queries, tested at production scale |

### Data Integrity Considerations

1. **Immutable billing records** - Once created, never modified
2. **Audit trail** - DailyRigUsage links to specific heartbeats
3. **Consistency** - Balance deduction happens after billing record creation

---

## Implementation Plan

### Phase 1: Data Collection Foundation

**Goal**: Create DailyRigUsage records for each online rig

**Tasks:**
1. Create `DailyRigUsage` model with migration
2. Update `IngestView` (`gpu_monitor/metrics_app/views.py`) to create DailyRigUsage on ONLINE status
3. Create `backfill_daily_usage.py` management command for historical data
4. Add index on `date` field for efficient daily queries

### Phase 2: Billing Logic

**Goal**: Calculate and apply daily charges

**Tasks:**
1. Create `BillingRecord` model with migration
2. Add `User.balance_cents` field with migration
3. Create `calculate_daily_billing.py` management command
   - Run daily at 02:00 UTC (after all heartbeats processed)
   - For each user: count distinct rigs with DailyRigUsage for yesterday
   - Create BillingRecord with charge amount
   - Subtract from User.balance_cents (TODO: negative balance handling)

### Phase 3: Dashboard & API

**Goal**: User-facing billing information

**Tasks:**
1. Create API endpoint `/api/v1/billing/history/`
2. Create `BillingHistoryView` with monthly totals
3. Add dashboard tab "Billing" showing:
   - Current balance ($12.34)
   - This month's estimated charge
   - Previous billing records
4. Add Django URL routing

### Phase 4: Payment Processing (Future)

**Goal**: Allow users to add funds

**Tasks:**
1. Add `stripe_customer_id` to User model
2. Create payment session endpoint
3. Add webhook handler for payment events
4. Add "Add Credit" button in dashboard

---

## Query Examples

### Daily Active Rigs Count

```python
from datetime import date, timedelta

def get_daily_active_rigs(user, date_obj=None):
    """Count rigs that were online on the given date."""
    if date_obj is None:
        date_obj = date.today() - timedelta(days=1)
    
    return DailyRigUsage.objects.filter(
        rig__owner=user,
        date=date_obj
    ).values('rig').distinct().count()
```

### Monthly Total Rig-Days

```python
def get_monthly_rig_days(user, year, month):
    """Get total rig-days used for a month."""
    from django.db.models import Sum
    
    period_start = date(year, month, 1)
    # Calculate period_end as last day of month
    
    return DailyRigUsage.objects.filter(
        rig__owner=user,
        date__month=month,
        date__year=year
    ).aggregate(
        total_days=Sum('online_count')
    )['total_days'] or 0
```

### User Balance Check

```python
def check_sufficient_balance(user, estimated_cost_cents):
    """Check if user can be charged for estimated period."""
    return user.balance_cents >= estimated_cost_cents
```

---

## Timing Considerations

| Event | When | Details |\n|-------|------|---------|\n| Heartbeat | Every 60s | Agent sends data to `/api/v1/ingest/` |\n| DailyRigUsage creation | On heartbeat | If status='online', create/get DailyRigUsage for today |\n| Billing calculation | 02:00 UTC daily | Count yesterday's DailyRigUsage, create BillingRecord |\n| Balance deduction | After billing | Subtract amount from user.balance_cents |\n| Dashboard refresh | On page load | Show current balance and pending charges |

---

## Files to Create/Modify

| Action | File | Description |\n|--------|------|-------------|\n| Create | `gpu_monitor/rigs/models.py` | Add DailyRigUsage model |\n| Modify | `gpu_monitor/accounts/models.py` | Add balance_cents, BillingRecord |\n| Modify | `gpu_monitor/metrics_app/views.py` | Update IngestView |\n| Create | `gpu_monitor/dashboard/views.py` | Add BillingHistoryView |\n| Create | `gpu_monitor/dashboard/urls.py` | Add billing endpoints |\n| Create | `gpu_monitor/templates/dashboard/` | Add billing tab |\n| Create | `management/commands/calculate_daily_billing.py` | Daily billing job |\n| Create | `management/commands/backfill_daily_usage.py` | Historical data backfill |

---

## Risk Assessment

| Risk | Impact | Mitigation |\n|------|--------|------------|\n| Duplicate DailyRigUsage | Data corruption | Unique constraint + get_or_create() |\n| Race conditions | Lost data | Atomic database operations |\n| Missing heartbeats | Under-billing | Still charge if any ONLINE event |\n| Negative balances | Revenue loss | Set minimum threshold (future) |\n| Timezone confusion | Wrong billing date | Always use UTC dates |\n| Performance with scale | Slow queries | Proper indexing, tested at production scale |

---

## Future Extensions

1. **Free tier**: X rigs free for Y days
2. **Usage alerts**: Email when daily cost exceeds threshold
3. **Usage tiers**: Volume discounts for >100 rigs
4. **Prepaid credits**: Buy $100 credit, get 10% bonus
5. **API limits**: Rate limit on heartbeats based on balance