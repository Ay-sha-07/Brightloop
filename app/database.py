from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load .env from project root (parent of app/)
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# Project root = parent of the "app" package
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Prefer Supabase / Postgres via DATABASE_URL.
# Fallback to local SQLite so the app still runs offline for demos.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if DATABASE_URL:
    # Supabase connection strings often use postgres:// — SQLAlchemy 2 prefers postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    # Optional: use the connection pooler (port 6543) for serverless; direct is fine for local.
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
else:
    DB_PATH = os.path.join(DATA_DIR, "brightloop.db")
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
