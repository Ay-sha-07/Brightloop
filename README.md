# Brightloop Subscription Management System

Unified platform for plans, subscriptions, recurring billing, invoices, payments and revenue reporting.

## Stack

- **FastAPI** + **SQLAlchemy** + **SQLite** (local, zero-config)
- Server-rendered UI with Jinja2
- Neo-brutalist / Swiss enterprise aesthetic (IBM Plex, hard borders, high contrast)
- JWT cookie auth with roles: Admin · Staff · Customer

## Quick start

```bash
cd subscription-system
pip install -r requirements.txt
# or: python -m pip install --user -r requirements.txt

export PATH="$HOME/.local/bin:$PATH"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open http://localhost:8000

### Demo accounts

| Role     | Email                  | Password    |
|----------|------------------------|-------------|
| Admin    | admin@brightloop.io    | Admin@1234  |
| Staff    | staff@brightloop.io    | Staff@1234  |
| Customer | customer@example.com   | Customer@1  |

## Features implemented

- **Auth**: email uniqueness, strong password rules (length + upper + lower + special), role-based access, cookie JWT
- **Products & variants**: name, type, sales/cost price, notes, brand-style variants with price extras
- **Plans**: daily/weekly/monthly/yearly, min quantity, availability windows, auto-close / pause / renew flags
- **Subscriptions**: numbered, status flow Draft → Quotation → Confirmed → Active → Closed/Paused, lines with product/qty/tax
- **Invoices**: auto-generated from active/confirmed subscriptions, tax calculation, status Draft → Confirmed → Paid, no double-billing same day
- **Payments**: method, amount, date, note; auto-mark paid when fully settled
- **Discounts**: fixed or %, min purchase/qty, usage limits, product or subscription scope (admin only)
- **Users**: only admin can add staff/admin
- **Dashboard**: active count, revenue, outstanding, overdue, monthly payments — designed for sub-2s response on thousands of rows (indexed queries, aggregates)
- **Customer portal**: customers see only their own subscriptions and invoices

## Design notes

- Local SQLite database at `data/brightloop.db` — works fully offline
- No external cloud dependencies for core operation
- Validation on every form (password strength, positive amounts, status transitions)
- Navigation and spacing kept consistent; responsive below 800px

## Project layout

```
app/
  main.py          # routes + business logic
  models.py        # SQLAlchemy models
  auth.py          # password hashing, JWT, role guards
  schemas.py       # Pydantic models
  database.py      # engine + session
  templates/       # Jinja2 pages
  static/css/      # neo-brutal stylesheet
data/              # SQLite file (auto-created)
```
