# Monetization System Design

## Overview

Implement daily billing at **$0.01 per rig** for any rig that was online ≥1 time during that day.

Track:
- Daily active rig count per user
- Monthly billing cycles
- Credit balance and charges

## Current System Analysis

### Rig Status Tracking (Already Exists)

**Rig model (lines 20-80):**
- `last_seen`: DateTime - most recent heartbeat
- `status`: 'online'/'stale'/'offline' - computed from last_seen
- Status updates: ONLINE ≤2min, STALE >2min & ≤10min, OFFLINE >10min

**RigStatusEvent (lines 309-329):**
- Tracks every status change
- Includes timestamp, rig_uuid, status, previous_status
- Already enables uptime analysis

**Problem**: RigStatusEvent only created on status changes, not daily summaries.

### What We Need to Add

| Component | Purpose |\n|-----------|---------|\n| DailyRigUsage model | Track "rig was online on date X" |\n| BillingRecord model | Monthly charges, payment tracking |\n| User.balance field | Current credit balance (decimal) |\n| Management command | Daily aggregation of active rigs |\n| Dashboard view | Usage/Billing history |\n| Stripe integration | Payment processing (optional) |

## Monetization Approaches (Industry Patterns)

### Approach 1: Daily Active Rig Count (Recommended)

**Logic:**
- Daily: `COUNT(DISTINCT rig_uuid WHERE status='online' AND date=today)`
- Charge: `online_rigs × $0.01`
- Track in DailyRigUsage model

**Pros:** Simple, aligns with "$0.01 per day per rig online" requirement
**Cons:** Must preserve online events (can't just use last_seen)

### Approach 2: Heartbeat Count

**Logic:**
- Count heartbeats per day, divide by 1440 (minutes)
- If count > 0, rig was online that day
- Charge: same as above

**Pros:** More accurate uptime percentage
**Cons:** Requires parsing all heartbeats (expensive)

### Approach 3: Last Seen Date Window

**Logic:**
- Check if `last_seen >= start_of_day` for each rig
- If yes, rig was online today

**Pros:** Simple query, uses existing data
**Cons:** Missed if rig offline at midnight but was online earlier

### Recommendation: DailyRigUsage Model

Best approach combines approaches 1 and 3:
- Create DailyRigUsage at every ONLINE heartbeat
- Query: `COUNT(DISTINCT rig_uuid WHERE date=X AND status='online')`

## Database Schema Changes

### 1. User Model Addition

```python
# accounts/models.py
class User(AbstractUser):
    # ... existing fields ...
    
    # Current credit balance in USD (for billing)
    balance_cents = models.PositiveIntegerField(default=0, help_text="Credit balance in cents")
```

### 2. DailyRigUsage Model (NEW)

```python
# rigs/models.py
class DailyRigUsage(models.Model):
    """One row per rig per day when rig was online at least once."""
    rig = models.ForeignKey('rigs.Rig', on_delete=models.CASCADE)
    date = models.DateField(db_index=True)  # The day being tracked
    online_count = models.PositiveIntegerField(default=1)  # How many times came online
    
    class Meta:
        db_table = 'rigs_daily_usage'
        unique_together = ('rig', 'date')  # One row per rig per day
        indexes = [
            models.Index(fields=['rig', '-date']),
            models.Index(fields=['date']),  # For daily aggregation
        ]
```

### 3. BillingRecord Model (NEW)

```python
# accounts/models.py
class BillingRecord(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    period_start = models.DateField()  # Month start (2026-07-01)
    period_end = models.DateField()    # Month end (2026-07-31)
    rig_days_used = models.PositiveIntegerField()  # Sum of days
    amount_cents = models.IntegerField()  # USD cents (negative = charge)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'accounts_billing_record'
        unique_together = ('user', 'period_start')
```

## Edge Cases & Solutions

| Edge Case | Solution |\n|-----------|----------|\n| Rig goes offline before midnight | DailyRigUsage created on first ONLINE heartbeat |\n| User has >1000 rigs | Query with date index, pagination |\n| Timezone handling | Use UTC date, user timezone for display only |\n| Midnight boundary | Check `last_seen >= date_midnight_utc` |\n| Missing heartbeats | Still counts if at least one ONLINE event |\n| Rig transferred between users | New owner pays from transfer date onward |\n| User deletes rig mid-day | DailyRigUsage still counted for that day |\n| Refunds/credits | Add CreditAdjustment model with positive amounts |\n| Failed billing | Retry logic, email notification after 3 failures |\n| Concurrent heartbeat processing | Use `select_for_update()` on DailyRigUsage |\n| Duplicate heartbeats | Unique constraint prevents duplicates |\n| System clock drift | Rely on server timestamp, not rig timestamp |\n| Free tier (if introduced) | Track in user.plan field (future) |

## Implementation Plan

### Phase 1: Data Collection (DailyRigUsage)

1. **Create model** `DailyRigUsage` with migration
2. **Update IngestView** to create DailyRigUsage on ONLINE status
3. **Add index** for efficient daily queries
4. **Create management command** `backfill_daily_usage.py` for historical data

### Phase 2: Billing Logic

1. **Create model** `BillingRecord` with migration
2. **Add User.balance_cents** field with migration
3. **Create management command** `calculate_daily_billing.py`
   - Run at 02:00 UTC daily
   - For each user: `SUM(online_count)` across all rigs for yesterday
   - Create BillingRecord if amount > 0
   - Subtract from User.balance_cents

### Phase 3: Dashboard

1. **Create API endpoint** `/api/v1/billing/history/`
2. **Create view** `BillingHistoryView` with monthly totals
3. **Add dashboard tab** "Billing" showing:
   - Current balance
   - This month's estimated charge
   - Historical records

### Phase 4: Payment Integration (Optional)

1. **Add Stripe customer ID** to User model
2. **Create webhook endpoint** for payment events
3. **Add "Add Credit" button** in dashboard

## Query Examples

### Daily Active Rigs per User (for billing)
```python
from datetime import date

yesterday = date.today() - timedelta(days=1)

DailyRigUsage.objects.filter(
    rig__owner=user,
    date=yesterday
).values('rig').distinct().count()
```

### Monthly Total Rig-Days
```python
month_start = date(today.year, today.month, 1)

BillingRecord.objects.filter(
    user=user,
    period_start__gte=month_start
).aggregate(total_days=Sum('rig_days_used'))
```

## Pricing Model Notes

- **$0.01 per rig per day** = $30/month per rig
- Example: 5 rigs online all month = $150
- Balance deducted daily or monthly (recommend monthly for simplicity)
- Minimum charge period: 1 day (even if rig offline 1 second)

## Files to Modify/Create

| File | Change |\n|------|--------|\n| `rigs/models.py` | Add DailyRigUsage model |\n| `accounts/models.py` | Add balance_cents to User, add BillingRecord model |\n| `metrics_app/views.py` | Update IngestView to create DailyRigUsage |\n| `dashboard/views.py` | Add BillingHistoryView |\n| `management/commands/` | Create `calculate_daily_billing.py` |\n| `dashboard/urls.py` | Add billing endpoint |\n| `templates/dashboard/` | Add billing tab template |