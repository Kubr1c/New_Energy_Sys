"""Database runtime configuration for the visualization API.

The project can still run in the historical file-backed mode when
``NES_DATABASE_URL`` is unset.  Once the variable is configured, all display
data reads are expected to come from the database and missing imports should
surface as explicit API errors.
"""

from __future__ import annotations

import functools
import os
from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

try:  # Optional at import time; dependency is declared for real DB runs.
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - only used when python-dotenv is absent.
    load_dotenv = None


def _load_env_file() -> None:
    if load_dotenv is not None:
        load_dotenv()


def get_database_url() -> str | None:
    """Return the configured SQLAlchemy URL, or ``None`` for file mode."""
    _load_env_file()
    url = os.environ.get("NES_DATABASE_URL", "").strip()
    return url or None


def is_database_enabled() -> bool:
    """Whether API reads should use the database repository."""
    return get_database_url() is not None


@functools.lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Create the process-wide SQLAlchemy engine.

    MySQL is the target runtime, but the models avoid MySQL-only constructs so
    unit tests can exercise the repository against SQLite.
    """
    url = get_database_url()
    if not url:
        raise RuntimeError("NES_DATABASE_URL is not configured.")
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)


@functools.lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a session and close it after use."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Clear cached DB objects; tests use this after changing env vars."""
    get_session_factory.cache_clear()
    get_engine.cache_clear()
