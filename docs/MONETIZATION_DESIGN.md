# Monetization System Design

## Executive Summary

Implement a usage-based billing system charging **$0.01 per rig per day** when a rig reports at least one heartbeat during that calendar day. This document outlines the chosen approach (DailyRigUsage) and provides implementation details.

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
1. **Daily aggregation** - Cannot query "how many rigs were online on date X"
2. **Billing ledger** - No record of charges or balance tracking
3. **User-facing billing view** - No way for users to see costs

---

## Monetization Approach Comparison

### Approach 1: Heartbeat Count Method
- Count all heartbeats per day, divide by minutes
- **Cons**: Expensive queries, fractional cents rounding issues

### Approach 2: Last Seen Date Window
- Check if `last_seen >= start_of_day`
- **Cons**: Misses rigs offline at midnight but online earlier

### Approach 3: Incremental Per-Payload
- Charge `0.01/1440` cents on each heartbeat
- **Cons**: Unauditable charges, hard refunds, negative balances mid-day

### Approach 4: Daily Active Rig Count (CHOSEN)
- Create DailyRigUsage record on each ONLINE heartbeat
- Bill once per day: `rigs_online × $0.01`

**Why DailyRigUsage wins:**
1. **Auditability**: Users can see exactly which days their rigs were billed
2. **Refunds**: Delete DailyRigUsage to reverse erroneous charges
3. **Race safe**: PostgreSQL unique constraints prevent duplicates
4. **Simple queries**: Daily aggregation is straightforward
5. **Minimal storage**: One row per rig per day

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
    """Tracks daily rig activity for billing.
    
    One row per rig per day. Created when rig is online at ANY point that day.
    Enables accurate billing and refund capabilities.
    """
    rig = models.ForeignKey('rigs.Rig', on_delete=models.CASCADE, related_name='daily_usage')
    date = models.DateField(db_index=True)
    # Note: We don't store 'online_count' since we only bill once per day per rig
    
    class Meta:
        db_table = 'rigs_daily_usage'
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
    """Monthly billing record for rig monitoring costs."""
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

## Implementation Plan (DailyRigUsage Approach)

### Phase 1: Model Creation

1. Add `balance_cents` to User model
2. Create `DailyRigUsage` model
3. Create `BillingRecord` model
4. Run `./manage.py makemigrations`

### Phase 2: IngestView Integration

**File: `gpu_monitor/metrics_app/views.py`** (after line ~640 where rig.save() happens)

```python
# After rig.save() in IngestView.post()
if rig.status == Rig.Status.ONLINE:
    from django.utils import timezone
    from gpu_monitor.rigs.models import DailyRigUsage
    
    today = timezone.now().date()
    DailyRigUsage.objects.get_or_create(rig=rig, date=today)
```

### Phase 3: Billing Management Command

**File: `gpu_monitor/management/commands/calculate_billing.py`**

```python
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, F
from gpu_monitor.rigs.models import DailyRigUsage
from gpu_monitor.accounts.models import BillingRecord

DAILY_RATE_CENTS = 1  # $0.01

class Command(BaseCommand):
    def handle(self, *args, **options):
        yesterday = timezone.now().date() - timedelta(days=1)
        
        # Group by user, count distinct rigs for yesterday
        usage_by_user = DailyRigUsage.objects.filter(
            date=yesterday
        ).values('rig__owner').annotate(
            rig_count=Count('rig', distinct=True)
        )
        
        for item in usage_by_user:
            user_id = item['rig__owner']
            rig_count = item['rig_count']
            amount = -rig_count * DAILY_RATE_CENTS
            
            # Skip if already billed
            if BillingRecord.objects.filter(
                user_id=user_id,
                period_start=yesterday
            ).exists():
                continue
            
            # Create billing record
            BillingRecord.objects.create(
                user_id=user_id,
                period_start=yesterday,
                period_end=yesterday,
                rig_days_used=rig_count,
                amount_cents=amount
            )
            
            # Deduct from balance atomically
            User.objects.filter(id=user_id).update(
                balance_cents=F('balance_cents') - amount
            )
```

### Phase 4: Cron Scheduling

Add to cron configuration:
```
0 2 * * * /opt/monitoring-platform/manage.py calculate_billing
```

### Phase 5: Dashboard API

**File: `gpu_monitor/dashboard/views.py`**

```python
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils import timezone
from gpu_monitor.rigs.models import DailyRigUsage
from gpu_monitor.accounts.models import BillingRecord

class BillingHistoryView(APIView):
    def get(self, request):
        user = request.user
        today = timezone.now().date()
        
        # Current month usage
        this_month = DailyRigUsage.objects.filter(
            rig__owner=user,
            date__month=today.month,
            date__year=today.year
        ).count()
        
        # Billing history
        history = BillingRecord.objects.filter(
            user=user
        ).values(
            'period_start',
            'period_end', 
            'rig_days_used',
            'amount_cents'
        )[:12]
        
        return Response({
            'balance_cents': user.balance_cents,
            'days_this_month': this_month,
            'estimated_monthly_cents': this_month * 30 * DAILY_RATE_CENTS,
            'history': list(history)
        })
```

---

## Edge Cases Handled

| Edge Case | Solution |\n|-----------|----------|\n| Rig offline at midnight | Still billed if ONLINE heartbeat occurred |\n| Midnight boundary | UTC date of heartbeat used |\n| Multiple heartbeats/day | Unique constraint prevents duplicates |\n| Rig transfer | New owner pays from transfer date |\n| Rig deletion | Still billed if DailyRigUsage exists |\n| Timezone handling | All dates in UTC |\n| Concurrent heartbeats | `get_or_create()` is atomic |\n| System clock drift | Server timestamp overrides rig timestamp |\n| Lost heartbeats | Still charge if any ONLINE event |\n| Refunds | Delete DailyRigUsage or BillingRecord |\n| >1000 rigs | Indexed queries |

---

## Risk Assessment

| Risk | Mitigation |\n|------|------------|\n| Duplicate DailyRigUsage | Unique constraint + get_or_create() |\n| Race conditions | Atomic database operations |\n| Negative balances | Email alert when balance < $1 |\n| Timezone issues | Always use UTC dates |\n| Performance | Index on DailyRigUsage.date field |