"""
database/official_db.py

Manages official_kindergartens.db ??the authoritative source of New Taipei
public kindergarten records scraped from government / official data sources.

Engine: SQLite via SQLAlchemy 2.x Core (no ORM).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import (
    Column,
    Connection,
    Engine,
    Index,
    Integer,
    MetaData,
    REAL,
    Table,
    Text,
    create_engine,
    event,
    text,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_DEFAULT_DB_PATH = _HERE.parent / "data" / "official_kindergartens.db"

# Module-level singleton engine (initialised lazily)
_engine: Engine | None = None

# Shared metadata registry for this database
metadata = MetaData()

# ---------------------------------------------------------------------------
# Table definition
# ---------------------------------------------------------------------------

kindergartens_table = Table(
    "kindergartens",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("official_source_id", Text),
    Column("official_name", Text, nullable=False),
    Column("school_name", Text),
    Column("kindergarten_name", Text),
    Column("kindergarten_type", Text),
    Column("public_type", Text),
    Column("district", Text, nullable=False),
    Column("address", Text),
    Column("postal_code", Text),
    Column("phone", Text),
    Column("latitude", REAL),
    Column("longitude", REAL),
    Column("official_source", Text),
    Column("official_source_url", Text),
    Column("is_public", Integer, default=1),
    Column("status", Text, default="ACTIVE"),
    Column("created_at", Text),
    Column("updated_at", Text),
)

# Explicit indexes (in addition to the implicit PK index)
Index("ix_kindergartens_district", kindergartens_table.c.district)
Index("ix_kindergartens_official_name", kindergartens_table.c.official_name)
Index("ix_kindergartens_is_public", kindergartens_table.c.is_public)


# ---------------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------------


def get_engine(db_path: str | Path | None = None) -> Engine:
    """Return (and cache) the SQLAlchemy engine for official_kindergartens.db.

    Args:
        db_path: Explicit path to the SQLite file.  Defaults to
                 ``<project_root>/data/official_kindergartens.db``.
                 Can also be set via the environment variable
                 ``OFFICIAL_DB_PATH``.

    Returns:
        A connected :class:`sqlalchemy.Engine` instance.
    """
    global _engine

    if _engine is not None:
        return _engine

    # Resolution order: explicit arg > env var > default path
    resolved = (
        Path(db_path)
        if db_path
        else Path(os.environ.get("OFFICIAL_DB_PATH", str(_DEFAULT_DB_PATH)))
    )
    resolved.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{resolved}",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    # Enable WAL mode and foreign-key enforcement for every new connection
    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    _engine = engine
    return _engine


def create_tables(db_path: str | Path | None = None) -> None:
    """Create all tables (and indexes) if they do not already exist.

    This is idempotent ??safe to call on every application start-up.

    Args:
        db_path: Forwarded to :func:`get_engine`.
    """
    engine = get_engine(db_path)
    metadata.create_all(engine)


def get_connection(db_path: str | Path | None = None) -> Connection:
    """Return a raw SQLAlchemy 2.x :class:`~sqlalchemy.Connection`.

    The caller is responsible for committing / rolling back and closing the
    connection (use as a context manager):

    .. code-block:: python

        with get_connection() as conn:
            result = conn.execute(select(kindergartens_table))

    Args:
        db_path: Forwarded to :func:`get_engine`.

    Returns:
        An open :class:`sqlalchemy.Connection`.
    """
    return get_engine(db_path).connect()


# ---------------------------------------------------------------------------
# Convenience query helpers
# ---------------------------------------------------------------------------


def get_all_kindergartens(db_path: str | Path | None = None) -> list[dict]:
    """Fetch every row from the kindergartens table as plain dicts."""
    with get_connection(db_path) as conn:
        rows = conn.execute(kindergartens_table.select()).mappings().all()
        return [dict(row) for row in rows]


def get_kindergartens_by_district(
    district: str, db_path: str | Path | None = None
) -> list[dict]:
    """Fetch all kindergartens in *district*."""
    with get_connection(db_path) as conn:
        stmt = kindergartens_table.select().where(
            kindergartens_table.c.district == district
        )
        rows = conn.execute(stmt).mappings().all()
        return [dict(row) for row in rows]


def upsert_kindergarten(record: dict, db_path: str | Path | None = None) -> int:
    """Insert or replace a kindergarten record.

    Uses SQLite's ``INSERT OR REPLACE`` semantics via SQLAlchemy's
    ``insert ??on conflict`` style.  The ``official_source_id`` field is used
    as the natural deduplication key; if it is absent the record is always
    inserted as a new row.

    Args:
        record: Mapping of column name ??value.  Unknown keys are ignored.
        db_path: Forwarded to :func:`get_engine`.

    Returns:
        The ``lastrowid`` of the inserted / replaced row.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    allowed_cols = {c.key for c in kindergartens_table.columns}
    clean = {k: v for k, v in record.items() if k in allowed_cols}

    with get_connection(db_path) as conn:
        stmt = sqlite_insert(kindergartens_table).values(**clean)

        if clean.get("official_source_id"):
            stmt = stmt.on_conflict_do_update(
                index_elements=["official_source_id"],
                set_={k: v for k, v in clean.items() if k != "official_source_id"},
            )

        result = conn.execute(stmt)
        conn.commit()
        return result.lastrowid


# ---------------------------------------------------------------------------
# Module initialisation guard
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    create_tables()
    print("official_kindergartens.db ??tables created successfully.")

