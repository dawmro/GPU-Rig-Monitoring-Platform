# Monetization System Design

## Executive Summary

Implement a usage-based billing system charging **$0.01 per rig per day** when a rig reports at least one heartbeat during that calendar day.

---

## Pricing Model (CORRECTED)

| Plan | Cost Per Rig Per Day | Monthly Estimate (30 days) |\n|------|---------------------|---------------------------|\n| Pay-as-you-go | $0.01 | **$0.30** if online daily |

**Note:** $0.01 × 30 days = $0.30/month per rig (not $30)

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
        charge = rigs_online × 1 cent
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

## Detailed Implementation Plan with Code

### Phase 1: Models & Migrations

**Step 1.1: Add balance_cents to User model**
```python
# File: gpu_monitor/accounts/models.py
# Line ~12 (after electricity_rate_kwh field)

balance_cents = models.PositiveIntegerField(
    default=0,
    help_text="User credit balance in cents (USD)"
)
```

**Step 1.2: Create DailyRigUsage model**
```python
# File: gpu_monitor/rigs/models.py
# Add after Rig model (after line ~80)

class DailyRigUsage(models.Model):
    """Tracks daily rig activity for billing.
    
    One row per rig per day. Created when rig is online at ANY point that day.
    Enables accurate billing and refund capabilities.
    """
    rig = models.ForeignKey('rigs.Rig', on_delete=models.CASCADE, related_name='daily_usage')
    date = models.DateField(db_index=True)
    
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

**Step 1.3: Create BillingRecord model**
```python
# File: gpu_monitor/accounts/models.py
# Add at end of file (after ApiKey model)

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
    
    def __str__(self):
        return f"{self.user.email} - {self.period_start} to {self.period_end}"
```

**Step 1.4: Run migrations**
```bash
cd /opt/gpu_monitor
./manage.py makemigrations
./manage.py migrate
```

---

### Phase 2: IngestView Integration

**File: `gpu_monitor/metrics_app/views.py`** (around line 640, after `rig.save()`)

```python
# In IngestView.post() method, after rig.save() and status update:
# Lines ~637-645 in current code

from django.utils import timezone
from gpu_monitor.rigs.models import DailyRigUsage

# ... existing code ...

# After line 639: rig.save(update_fields=['last_seen', 'status'])
# Add this block:

if rig.status == Rig.Status.ONLINE:
    today = timezone.now().date()
    DailyRigUsage.objects.get_or_create(rig=rig, date=today)
```

**Why get_or_create()?** Atomic in PostgreSQL, prevents duplicates when concurrent heartbeats arrive.

---

### Phase 3: Billing Management Command

**File: `gpu_monitor/management/commands/calculate_billing.py`**

```python
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, F
from gpu_monitor.rigs.models import DailyRigUsage
from gpu_monitor.accounts.models import BillingRecord, User

DAILY_RATE_CENTS = 1  # $0.01

class Command(BaseCommand):
    help = 'Calculate daily billing for rigs that were online yesterday'
    
    def handle(self, *args, **options):
        yesterday = timezone.now().date() - timedelta(days=1)
        self.stdout.write(f"Calculating billing for {yesterday}")
        
        # Group by user, count distinct rigs for yesterday
        usage_by_user = DailyRigUsage.objects.filter(
            date=yesterday
        ).values('rig__owner').annotate(
            rig_count=Count('rig', distinct=True)
        )
        
        created = 0
        skipped = 0
        
        for item in usage_by_user:
            user_id = item['rig__owner']
            rig_count = item['rig_count']
            amount = -rig_count * DAILY_RATE_CENTS
            
            # Skip if already billed
            if BillingRecord.objects.filter(
                user_id=user_id,
                period_start=yesterday
            ).exists():
                skipped += 1
                continue
            
            # Create billing record
            BillingRecord.objects.create(
                user_id=user_id,
                period_start=yesterday,
                period_end=yesterday,
                rig_days_used=rig_count,
                amount_cents=amount
            )
            
            # Deduct from balance atomically using F()
            User.objects.filter(id=user_id).update(
                balance_cents=F('balance_cents') - amount
            )
            
            created += 1
            self.stdout.write(
                f"  Billed user #{user_id}: {rig_count} rigs = {amount} cents"
            )
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created} records created, {skipped} skipped"
            )
        )
```

---

### Phase 4: Cron Scheduling (CORRECTED PATH)

**System cron (recommended):**
```bash
# Run at 02:00 UTC daily
0 2 * * * /opt/gpu_monitor/manage.py calculate_billing >> /var/log/gpu_monitor/billing.log 2>&1
```

**Or Django-cron (if using):**
```python
# File: gpu_monitor/metrics_app/cron.py
from django_cron import CronJobBase, Schedule
from gpu_monitor.management.commands.calculate_billing import Command as BillingCommand

class DailyBillingJob(CronJobBase):
    schedule = Schedule(run_at_times=['02:00'])
    code = 'metrics.daily_billing'
    
    def do(self):
        cmd = BillingCommand()
        cmd.handle()
```

---

### Phase 5: Dashboard API Endpoint

**File: `gpu_monitor/dashboard/views.py`**

```python
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils import timezone
from gpu_monitor.rigs.models import DailyRigUsage
from gpu_monitor.accounts.models import BillingRecord

DAILY_RATE_CENTS = 1

class BillingHistoryView(APIView):
    """Return user's billing history and current balance."""
    
    def get(self, request):
        user = request.user
        today = timezone.now().date()
        
        # Current month usage (days where rigs were online)
        this_month = DailyRigUsage.objects.filter(
            rig__owner=user,
            date__month=today.month,
            date__year=today.year
        ).count()
        
        # Billing history (last 12 months)
        history = BillingRecord.objects.filter(
            user=user
        ).values(
            'period_start',
            'period_end', 
            'rig_days_used',
            'amount_cents'
        ).order_by('-period_start')[:12]
        
        # Current balance
        balance_dollars = user.balance_cents / 100.0
        
        return Response({
            'balance_cents': user.balance_cents,
            'balance_usd': f"${balance_dollars:.2f}",
            'days_this_month': this_month,
            'estimated_monthly_cents': this_month * 30 * DAILY_RATE_CENTS,
            'estimated_monthly_usd': f"${(this_month * 30 * DAILY_RATE_CENTS) / 100.0:.2f}",
            'history': list(history)
        })
```

**File: `gpu_monitor/dashboard/urls.py`**

```python
from django.urls import path
from .views import BillingHistoryView

urlpatterns = [
    # ... existing patterns ...
    path('api/v1/billing/history/', BillingHistoryView.as_view(), name='billing_history'),
]
```

---

### Phase 6: Backfill Command (for historical data)

**File: `gpu_monitor/management/commands/backfill_daily_usage.py`**

```python
from django.core.management.base import BaseCommand
from django.db import transaction
from gpu_monitor.rigs.models import DailyRigUsage
from gpu_monitor.metrics_app.models import MetricSnapshot

class Command(BaseCommand):
    help = 'Backfill DailyRigUsage from historical MetricSnapshot data'
    
    def add_arguments(self, parser):
        parser.add_argument('--start-date', type=str, help='Start date (YYYY-MM-DD)')
        parser.add_argument('--end-date', type=str, help='End date (YYYY-MM-DD)')
    
    def handle(self, *args, **options):
        qs = MetricSnapshot.objects.values('rig_uuid', 'timestamp').distinct()
        
        if options['start_date']:
            from datetime import datetime
            start = datetime.strptime(options['start_date'], '%Y-%m-%d').date()
            qs = qs.filter(timestamp__date__gte=start)
        
        if options['end_date']:
            from datetime import datetime
            end = datetime.strptime(options['end_date'], '%Y-%m-%d').date()
            qs = qs.filter(timestamp__date__lte=end)
        
        total = qs.count()
        self.stdout.write(f"Processing {total} snapshots...")
        
        created = 0
        with transaction.atomic():
            for i, snap in enumerate(qs.iterator()):
                DailyRigUsage.objects.get_or_create(
                    rig_id=snap['rig_uuid'],
                    date=snap['timestamp'].date()
                )
                created += 1
                if i % 10000 == 0:
                    self.stdout.write(f"  Processed {i}/{total}")
        
        self.stdout.write(
            self.style.SUCCESS(f"Done: {created} records created")
        )
```

**Usage:**
```bash
cd /opt/gpu_monitor
./manage.py backfill_daily_usage --start-date 2026-01-01 --end-date 2026-07-01
```

---

## Edge Cases Handled

| Edge Case | Solution |\n|-----------|----------|\n| Rig offline at midnight | Billed if ONLINE heartbeat occurred |\n| Midnight boundary | UTC date used |\n| Multiple heartbeats/day | Unique constraint prevents duplicates |\n| Rig transfer | New owner pays from transfer date |\n| Rig deletion | Still billed if DailyRigUsage exists |\n| Timezone handling | All dates in UTC |\n| Concurrent heartbeats | `get_or_create()` is atomic |\n| System clock drift | Server timestamp overrides |\n| Lost heartbeats | Still charge if any ONLINE event |\n| Refunds | Delete DailyRigUsage |\n| >1000 rigs | Indexed queries |

---

## Risk Assessment

| Risk | Mitigation |\n|------|------------|\n| Duplicate DailyRigUsage | Unique constraint + get_or_create() |\n| Race conditions | Atomic database operations |\n| Negative balances | Email alert when balance low |\n| Timezone issues | Always use UTC dates |\n| Performance | Index on date field |