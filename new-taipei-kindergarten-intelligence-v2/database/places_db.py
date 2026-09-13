"""
database/places_db.py

Manages google_places.db ??stores Google Places API match results for every
official kindergarten, including match scores and enriched place metadata.

Engine: SQLite via SQLAlchemy 2.x Core (no ORM).
"""

from __future__ import annotations

import os
from pathlib import Path

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
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_DEFAULT_DB_PATH = _HERE.parent / "data" / "google_places.db"

_engine: Engine | None = None

metadata = MetaData()

# ---------------------------------------------------------------------------
# Table definition
# ---------------------------------------------------------------------------

place_matches_table = Table(
    "place_matches",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kindergarten_id", Integer, nullable=False),
    Column("official_name", Text),
    Column("district", Text),
    Column("official_address", Text),
    Column("google_place_id", Text),
    Column("google_name", Text),
    Column("google_address", Text),
    Column("google_latitude", REAL),
    Column("google_longitude", REAL),
    Column("google_rating", REAL),
    Column("google_user_rating_count", Integer),
    Column("google_maps_uri", Text),
    Column("business_status", Text),
    Column("match_score", REAL),
    Column("name_score", REAL),
    Column("address_score", REAL),
    Column("geo_score", REAL),
    Column("match_status", Text, default="PENDING"),
    Column("match_method", Text),
    Column("raw_json", Text),
    Column("collection_scope", Text, default="GOOGLE_PLACES_API_PARTIAL"),
    Column("first_seen_at", Text),
    Column("last_seen_at", Text),
)

# Explicit indexes
Index("ix_place_matches_kindergarten_id", place_matches_table.c.kindergarten_id)
Index("ix_place_matches_district", place_matches_table.c.district)
Index("ix_place_matches_google_place_id", place_matches_table.c.google_place_id)
Index("ix_place_matches_match_status", place_matches_table.c.match_status)


# ---------------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------------


def get_engine(db_path: str | Path | None = None) -> Engine:
    """Return (and cache) the SQLAlchemy engine for google_places.db.

    Args:
        db_path: Explicit path to the SQLite file.  Defaults to
                 ``<project_root>/data/google_places.db``.
                 Can also be set via the environment variable
                 ``PLACES_DB_PATH``.

    Returns:
        A connected :class:`sqlalchemy.Engine` instance.
    """
    global _engine

    if _engine is not None:
        return _engine

    resolved = (
        Path(db_path)
        if db_path
        else Path(os.environ.get("PLACES_DB_PATH", str(_DEFAULT_DB_PATH)))
    )
    resolved.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{resolved}",
        echo=False,
        connect_args={"check_same_thread": False},
    )

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

    Idempotent ??safe to call on every application start-up.

    Args:
        db_path: Forwarded to :func:`get_engine`.
    """
    engine = get_engine(db_path)
    metadata.create_all(engine)


def get_connection(db_path: str | Path | None = None) -> Connection:
    """Return a raw SQLAlchemy 2.x :class:`~sqlalchemy.Connection`.

    Usage::

        with get_connection() as conn:
            rows = conn.execute(place_matches_table.select()).mappings().all()

    Args:
        db_path: Forwarded to :func:`get_engine`.

    Returns:
        An open :class:`sqlalchemy.Connection`.
    """
    return get_engine(db_path).connect()


# ---------------------------------------------------------------------------
# Convenience query helpers
# ---------------------------------------------------------------------------


def get_place_match_by_kindergarten_id(
    kindergarten_id: int, db_path: str | Path | None = None
) -> dict | None:
    """Return the best (highest match_score) place match for a kindergarten.

    Args:
        kindergarten_id: FK reference to the official kindergartens table.
        db_path: Forwarded to :func:`get_engine`.

    Returns:
        Row as a plain dict, or ``None`` if no match exists.
    """
    from sqlalchemy import desc

    with get_connection(db_path) as conn:
        stmt = (
            place_matches_table.select()
            .where(place_matches_table.c.kindergarten_id == kindergarten_id)
            .order_by(desc(place_matches_table.c.match_score))
            .limit(1)
        )
        row = conn.execute(stmt).mappings().first()
        return dict(row) if row else None


def get_pending_matches(db_path: str | Path | None = None) -> list[dict]:
    """Return all rows whose ``match_status`` is ``'PENDING'``."""
    with get_connection(db_path) as conn:
        stmt = place_matches_table.select().where(
            place_matches_table.c.match_status == "PENDING"
        )
        rows = conn.execute(stmt).mappings().all()
        return [dict(row) for row in rows]


def upsert_place_match(record: dict, db_path: str | Path | None = None) -> int:
    """Insert or update a place match record.

    Deduplication key: ``(kindergarten_id, google_place_id)``.
    If either field is absent the record is always inserted as a new row.

    Args:
        record: Mapping of column name ??value.
        db_path: Forwarded to :func:`get_engine`.

    Returns:
        The ``lastrowid`` of the inserted / updated row.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    allowed_cols = {c.key for c in place_matches_table.columns}
    clean = {k: v for k, v in record.items() if k in allowed_cols}

    with get_connection(db_path) as conn:
        stmt = sqlite_insert(place_matches_table).values(**clean)

        if clean.get("kindergarten_id") and clean.get("google_place_id"):
            stmt = stmt.on_conflict_do_update(
                index_elements=["kindergarten_id", "google_place_id"],
                set_={
                    k: v
                    for k, v in clean.items()
                    if k not in ("kindergarten_id", "google_place_id")
                },
            )

        result = conn.execute(stmt)
        conn.commit()
        return result.lastrowid


# ---------------------------------------------------------------------------
# Module initialisation guard
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    create_tables()
    print("google_places.db ??tables created successfully.")

