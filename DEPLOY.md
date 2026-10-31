# Deploy — Supabase only

This app **does not use SQLite**. It requires `DATABASE_URL` pointing at Supabase Postgres.

## Local

```bash
cp .env.example .env
# edit DATABASE_URL with your Supabase URI + password
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Hosting (Render / Railway / Fly / Vercel / etc.)

1. Create the web service from this repo.
2. Set environment variables (do **not** commit `.env`):
   - `DATABASE_URL` = Supabase URI (with password, `?sslmode=require` recommended)
   - `SECRET_KEY` = long random string
3. Build / start command examples:
   - Install: `pip install -r requirements.txt`
   - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. On first boot, tables are created and demo data is seeded automatically.

## If you still see SQLite

- Old `DATABASE_URL` missing on the host → app would previously fall back to SQLite. That path is removed; missing URL now raises a clear error.
- Delete any local `data/brightloop.db` so it is not confused with production.
- Ensure the platform env var is named exactly `DATABASE_URL`.
