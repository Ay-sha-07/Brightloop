# Brightloop Subscription Management System

Unified platform for plans, subscriptions, recurring billing, invoices, payments and revenue reporting.

## Stack

- **FastAPI** + **SQLAlchemy** + **Supabase (Postgres)** by default (falls back to SQLite if `DATABASE_URL` is unset)
- Server-rendered UI with Jinja2
- Modern professional SaaS UI (Inter, soft shadows, indigo accent, subtle animations)
- JWT cookie auth with roles: Admin · Staff · Customer

## Quick start (local with SQLite)

```bash
cd Brightloop-main
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open http://localhost:8000

### Demo accounts

| Role     | Email                  | Password    |
|----------|------------------------|-------------|
| Admin    | admin@brightloop.io    | Admin@1234  |
| Staff    | staff@brightloop.io    | Staff@1234  |
| Customer | customer@example.com   | Customer@1  |

---

## Using Supabase as the database

### 1. Create a free Supabase project

1. Go to [https://supabase.com](https://supabase.com) and sign in (or create an account).
2. Click **New project**.
3. Choose an organization, name the project (e.g. `brightloop`), set a strong **database password** (save it), pick a region close to you, and create the project.
4. Wait until the project is ready (green status).

### 2. Get the connection string

1. In the Supabase dashboard open your project.
2. Go to **Project Settings** (gear icon) → **Database**.
3. Under **Connection string** choose **URI**.
4. Copy the string. It looks like:

   ```
   postgresql://postgres.[project-ref]:[YOUR-PASSWORD]@aws-0-[region].pooler.supabase.com:6543/postgres
   ```

   or the direct one:

   ```
   postgresql://postgres:[YOUR-PASSWORD]@db.[project-ref].supabase.co:5432/postgres
   ```

5. Replace `[YOUR-PASSWORD]` with the database password you set when creating the project.

**Tip:** For local development the **Session** or **Transaction** pooler (port `6543`) works well. For simple local runs the direct connection (port `5432`) is also fine.

### 3. Configure the app

```bash
cd Brightloop-main
cp .env.example .env
# Edit .env and paste your DATABASE_URL
```

Or export it in the shell:

```bash
export DATABASE_URL="postgresql://postgres:YOUR_PASSWORD@db.YOUR_REF.supabase.co:5432/postgres"
```

### 4. Install dependencies and run

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

On first start the app will:

- Create all tables in your Supabase Postgres database (`Base.metadata.create_all`)
- Seed demo users, products, plans, etc. (if the admin user does not exist yet)

You do **not** need to run SQL migrations manually for this prototype.

### 5. (Optional) Verify in Supabase

Open **Table Editor** in the Supabase dashboard. You should see tables such as `users`, `products`, `plans`, `subscriptions`, `invoices`, etc., and the seeded rows after the first run.

### Notes on Supabase

- The app uses SQLAlchemy against the **Postgres** database that Supabase provides. It does **not** use the Supabase JS client or Auth — authentication stays in the FastAPI JWT layer.
- Keep your `DATABASE_URL` (and especially the password) out of git. Add `.env` to `.gitignore` if it is not already there.
- If you see SSL errors, append `?sslmode=require` to the connection string.
- Connection limits: free tier has a modest connection pool; the app uses a small SQLAlchemy pool (`pool_size=5`).

---

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

- **Default database**: Supabase Postgres when `DATABASE_URL` is set; otherwise local SQLite at `data/brightloop.db`
- Modern professional UI with soft shadows, indigo accent, Inter font, and light entrance / hover animations
- Validation on every form (password strength, positive amounts, status transitions)
- Navigation and spacing kept consistent; responsive below 800px

## Project layout

```
app/
  main.py          # routes + business logic
  models.py        # SQLAlchemy models
  auth.py          # password hashing, JWT, role guards
  schemas.py       # Pydantic models
  database.py      # engine + session (Supabase / SQLite)
  templates/       # Jinja2 pages
  static/css/      # professional stylesheet + animations
data/              # SQLite file when DATABASE_URL is not set
.env.example       # template for Supabase connection string
```
