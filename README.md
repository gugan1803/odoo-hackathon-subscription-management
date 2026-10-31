# Brightloop Subscription Management System
Unified platform for products, plans, subscriptions, automated invoicing, discounts, payments and revenue reporting.

## Run
```bash
pip install -r requirements.txt
python app.py            # http://localhost:5000
python -c "import test_app as t; t.test_flow(); print('PASS')"   # tests
```
Default admin: `admin@brightloop.test` / `Admin@12345` (override with `ADMIN_PASSWORD`). Optional SMTP: `SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM` (otherwise emails print to console).

## Requirement coverage
| Requirement | Implementation |
|---|---|
| Unique email, password >8 w/ upper, lower, special | `/api/signup`, `pw_ok()`, UNIQUE constraint |
| Password reset link by email | `/api/forgot` + `/api/reset` (1h token, no account enumeration) |
| Roles admin / staff / customer; only admin adds staff | `@auth(...)` decorator, `/api/staff` |
| Products, variants (e.g. Odoo brand +560) | `products`, `variants` tables; price = base + extra |
| Plans: daily/weekly/monthly/yearly, min qty, dates, per-plan pause/close/renew | `plans` flags enforced in subscription actions |
| Quotation templates | `quote_templates`, `template_id` on create |
| Lifecycle Draft→Quotation→Confirmed→Active→Closed | `/api/subscriptions/<id>/<action>` with guarded transitions |
| Auto invoices, taxes, never twice/skipped | `/api/billing/run`; `UNIQUE(subscription_id, period_start)` makes it idempotent; next_billing advances per plan |
| Invoice Draft→Confirmed→Paid, confirm/cancel/send/print | `/api/invoices/<id>/<action>`; paid set automatically on full payment |
| Discounts (fixed/%, min amount/qty, dates, usage limit, per product/subscription), auto-applied | `active_discount()` inside billing; usage counted, stops on expiry/limit |
| Payments (method, amount, date, note), over-payment blocked | `/api/payments` |
| Dashboard: active subs, MRR, revenue, outstanding, overdue, filters | `/api/dashboard`, `/api/invoices?status=overdue` |
| <2s at scale | indexes on status/due date/customer, pagination (`page`,`size`), WAL mode, single-query aggregates |
| Customers see only their own data | `scope()` filter on subscriptions and invoices |
