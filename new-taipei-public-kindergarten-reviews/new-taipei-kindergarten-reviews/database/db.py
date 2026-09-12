"""
Database engine and session management.

Supports SQLite (default) and PostgreSQL via DATABASE_URL.
Uses SQLAlchemy 2.x style session factory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.models import Base


def _get_database_url() -> str:
    """
    Resolve DATABASE_URL from environment or fall back to default SQLite path.
    Ensures the data directory exists for SQLite databases.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        # Default: SQLite in data/ directory relative to project root
        project_root = Path(__file__).resolve().parent.parent
        db_path = project_root / "data" / "kindergarten_reviews.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path}"
    return url


def _configure_sqlite(engine: Engine) -> None:
    """Apply SQLite-specific PRAGMA settings for reliability and performance."""

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def build_engine(database_url: str | None = None) -> Engine:
    """
    Build and return a SQLAlchemy Engine.

    Args:
        database_url: Override DATABASE_URL. If None, reads from environment.

    Returns:
        Configured SQLAlchemy Engine.
    """
    url = database_url or _get_database_url()
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        engine = create_engine(url, connect_args=connect_args, echo=False)
        _configure_sqlite(engine)
    else:
        # PostgreSQL / other: connection pooling defaults are fine
        engine = create_engine(url, pool_pre_ping=True, echo=False)
    return engine


def init_db(engine: Engine | None = None) -> Engine:
    """
    Create all tables if they do not exist.

    Args:
        engine: Existing engine. If None, one is built from environment.

    Returns:
        The engine used (callers may cache it).
    """
    if engine is None:
        engine = build_engine()
    Base.metadata.create_all(engine)
    return engine


# ---------------------------------------------------------------------------
# Session factory (module-level singleton, lazily initialised)
# ---------------------------------------------------------------------------

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    """Return the module-level engine, initialising it on first call."""
    global _engine
    if _engine is None:
        _engine = build_engine()
        Base.metadata.create_all(_engine)
    return _engine


def get_session_factory() -> sessionmaker:
    """Return the module-level session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _SessionLocal


def get_session() -> Generator[Session, None, None]:
    """
    Dependency-injection compatible session generator.

    Usage::

        with get_session() as session:
            session.add(obj)
            session.commit()
    """
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_size_bytes(engine: Engine | None = None) -> int:
    """
    Return the database file size in bytes (SQLite only).
    Returns 0 for non-SQLite databases.
    """
    eng = engine or get_engine()
    url_str = str(eng.url)
    if "sqlite" in url_str:
        # Extract file path from URL like sqlite:////abs/path/file.db
        db_path_str = url_str.replace("sqlite:///", "")
        db_path = Path(db_path_str)
        if db_path.exists():
            return db_path.stat().st_size
    return 0
