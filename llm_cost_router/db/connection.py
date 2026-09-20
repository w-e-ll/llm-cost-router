import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)


def database_url():
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise ValueError("DATABASE_URL is not configured.")
    return value


def build_engine(value):
    url = make_url(value)
    if url.get_backend_name() not in ("postgres", "postgresql"):
        raise ValueError("A PostgreSQL DATABASE_URL is required.")
    url = url.set(drivername="postgresql+psycopg")
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
    )


@lru_cache(maxsize=1)
def get_engine():
    return build_engine(database_url())
