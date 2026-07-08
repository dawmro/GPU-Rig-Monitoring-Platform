# Monetization Design - Detailed Analysis & Gap Report

## Executive Summary

The design document covers core billing logic well but has **significant gaps** in:
- Frontend/UI implementation
- Negative balance handling
- Payment integration
- Notifications/alerts
- Admin tooling
- Testing strategy

---

## Backend Coverage Assessment

| Component | Status | Gaps |
|-----------|--------|------|
| Models (User, DailyRigUsage, BillingRecord) | ✅ Complete | None |
| IngestView integration | ✅ Code provided | Need to verify import paths |
| Billing management command | ✅ Code provided | No negative balance prevention |
| Backfill command | ✅ Code provided | None |
| Cron scheduling | ✅ Both options | Path corrected |
| Dashboard API | ✅ Code provided | No authentication/permissions shown |
| Payment processing | ❌ Missing | Stripe/Payment gateway not implemented |
| Webhooks | ❌ Missing | For payment confirmation |

---

## Frontend Coverage Assessment

| Component | Status | Notes |
|-----------|--------|-------|
| Dashboard billing tab | ❌ Missing | No template code |
| Balance display | ❌ Missing | No HTML/JS |
| Billing history table | ❌ Missing | No HTML/JS |
| Add credit button | ❌ Missing | No Stripe checkout integration |
| Usage charts | ❌ Missing | No Chart.js integration |
| Low balance alerts | ❌ Missing | No notification UI |

---

## Critical Edge Cases NOT Addressed

### 1. Negative Balance Handling
```python
# Current code ALLOWS negative balance:
User.objects.filter(id=user_id).update(
    balance_cents=F('balance_cents') - amount
)
# No check before deduction!
```

**Needed:**
- Prevent billing if balance < charge amount
- OR allow overdraft with grace period
- Email when balance goes negative
- Auto-suspend rigs when balance < threshold

### 2. Rig Transfer Between Users
- DailyRigUsage has `rig` FK but not `owner` at time of billing
- If rig transferred mid-month, who pays?
- Current logic uses `rig__owner` at billing time (correct for new owner)

### 3. Billing Period Alignment
- Currently daily records with `period_start = period_end = yesterday`
- Should we aggregate to monthly periods for cleaner records?
- Monthly period: first day to last day of month

### 4. Currency/Internationalization
- Hardcoded $0.01 and USD
- No support for different currencies
- Exchange rate handling missing

### 5. Free Tier / Promotional Credits
- No concept of free rigs
- No promo codes
- No trial periods

### 6. Prorated Billing
- User adds rig mid-day model has `electricity_rate_kwh` with decimal
- But billing is flat $0.01 - inconsistent precision

### 7. Race Conditions in Billing Command
```python
# Check exists then create - RACE CONDITION:
if BillingRecord.objects.filter(...).exists():
    continue
BillingRecord.objects.create(...)
# Another process could create between check and create
```

**Fix:** Use `get_or_create()` or `select_for_update()`

### 8. Idempotency of Billing Command
- Running command twice for same day creates duplicates
- Current check uses `period_start=yesterday` but command could be re-run

### 9. Timezone Display for Users
- All dates stored in UTC
- No user timezone preference
- Billing history shows UTC dates to users

### 10. Data Retention
- How long to keep DailyRigUsage?
- How long to keep BillingRecord?
- GDPR/privacy considerations

---

## Missing Backend Features

### A. Payment Integration (Stripe)
```python
# Missing models:
class PaymentIntent(models.Model):
    user = ForeignKey(User)
    stripe_intent_id = CharField()
    amount_cents = IntegerField()
    status = CharField()  # pending, succeeded, failed
    created_at = DateTimeField()

# Missing endpoints:
POST /api/v1/billing/create-payment-intent/
POST /api/v1/billing/stripe-webhook/
```

### B. Balance Alerts
```python
# Missing: Management command to check low balances
# Run hourly, send email if balance < $1
```

### C. Auto-Suspend on Negative Balance
```python
# Missing: Daily job to disable rigs with negative balance
# Rig.is_active = False when balance < -$5
```

### D. Admin Tools
```python
# Missing: Django admin for BillingRecord
# Missing: Manual credit adjustment endpoint
# Missing: Revenue reports
```

### E. API Permissions
```python
# Current BillingHistoryView has no permission classes
# Should use IsAuthenticated, maybe custom permissions
```

---

## Missing Frontend Features

### A. Dashboard Billing Tab (Template)
```html
<!-- Missing: templates/dashboard/billing.html -->
<!-- Should show: balance, usage chart, history table, add credit button -->
```

### B. Balance Card Component
```javascript
// Missing: Vue/HTMX component for real-time balance
// Poll /api/v1/billing/history/ every 5 min
```

### C. Add Credit Modal
```html
<!-- Missing: Stripe checkout integration -->
<!-- Button opens Stripe Checkout session -->
```

### D. Usage Visualization
```javascript
// Missing: Chart.js daily rig usage chart
// Show last 30 days, bars per day
```

### E. Email Templates
```html
<!-- Missing: templates/email/low_balance.html -->
<!-- Missing: templates/email/billing_receipt.html -->
<!-- Missing: templates/email/negative_balance.html -->
```

---

## Testing Gaps

| Test Type | Status |
|-----------|--------|
| Unit tests for billing command | ❌ Missing |
| Integration tests for ingest | ❌ Missing |
| Edge case tests (midnight, transfer) | ❌ Missing |
| Load tests (1000+ rigs) | ❌ Missing |
| Stripe webhook tests | ❌ Missing |

---

## Recommended Additions to Design Doc

### 1. Add Negative Balance Policy Section
```markdown
## Negative Balance Policy

### Option A: Hard Stop (Recommended)
- Prevent billing if balance < charge
- Return 402 Payment Required on ingest
- Rig status becomes 'suspended'

### Option B: Grace Period
- Allow overdraft up to -$5
- Email at -$1, -$3, -$5
- Auto-suspend at -$5

### Option C: Post-paid
- Allow negative, bill monthly
- Collections process for overdue
```

### 2. Add Monthly Aggregation Logic
```python
# Instead of daily BillingRecords, create monthly:
# Period: 1st to last day of month
# Single record per user per month
```

### 3. Add Stripe Integration Spec
```python
# PaymentIntent model
# Checkout session endpoint
# Webhook handler
# Success/cancel pages
```

### 4. Add Frontend Spec
```markdown
## Frontend Components

### Billing Tab Layout
- Balance card (top)
- Monthly estimated cost
- Usage chart (30 days)
- History table (paginated)
- Add Credit button

### API Endpoints Needed
GET /api/v1/billing/history/
POST /api/v1/billing/create-payment/
GET /api/v1/billing/current-usage/
```

---

## Updated Implementation Priority

| Priority | Task | Backend | Frontend |
|----------|------|---------|----------|
| P0 | Core models + ingest | ✅ | |
| P0 | Billing command + cron | ✅ | |
| P0 | Negative balance prevention | ❌ | |
| P0 | Dashboard API + permissions | ❌ | |
| P1 | Billing tab UI | | ❌ |
| P1 | Add credit (Stripe) | ❌ | ❌ |
| P1 | Low balance alerts | ❌ | |
| P1 | Monthly billing aggregation | ❌ | |
| P2 | Usage charts | | ❌ |
| P2 | Admin tools | ❌ | |
| P2 | Email templates | ❌ | ❌ |

---

## Conclusion

**Design is ~60% complete for backend, ~0% for frontend.**

Critical missing pieces:
1. Negative balance handling (business logic decision needed)
2. Payment integration (Stripe)
3. Frontend dashboard tab
4. Alerting/notifications
5. Testing strategy

**Recommendation:** Finalize negative balance policy before implementing billing command.