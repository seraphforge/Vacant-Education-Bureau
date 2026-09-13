"""
database/reviews_db.py

Manages reviews.db — stores Google Places API reviews collected for each
matched kindergarten.  A ``content_hash`` (SHA-256 of author + rating + text)
ensures de-duplication across incremental crawl runs.

Engine: SQLite via SQLAlchemy 2.x Core (no ORM).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import (
    Column,
    Connection,
    Engine,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_DEFAULT_DB_PATH = _HERE.parent / "data" / "reviews.db"

_engine: Engine | None = None

metadata = MetaData()

# ---------------------------------------------------------------------------
# Table definition
# ---------------------------------------------------------------------------

reviews_table = Table(
    "reviews",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kindergarten_id", Integer, nullable=False),
    Column("district", Text, nullable=False),
    Column("place_id", Text, nullable=False),
    Column("review_resource_name", Text),
    Column("author_display_name", Text),
    Column("author_uri", Text),
    Column("author_photo_uri", Text),
    Column("rating", Integer, nullable=False),
    Column("text", Text),
    Column("original_text", Text),
    Column("text_language", Text),
    Column("original_language", Text),
    Column("publish_time", Text),
    Column("relative_publish_time", Text),
    Column("review_uri", Text),
    Column("source", Text, default="GOOGLE_PLACES_API"),
    Column("content_hash", Text, nullable=False),
    Column("first_seen_at", Text),
    Column("last_seen_at", Text),
    Column("raw_json", Text),
    UniqueConstraint("content_hash", name="uq_reviews_content_hash"),
)

# Explicit indexes
Index("ix_reviews_kindergarten_id", reviews_table.c.kindergarten_id)
Index("ix_reviews_district", reviews_table.c.district)
Index("ix_reviews_place_id", reviews_table.c.place_id)
Index("ix_reviews_rating", reviews_table.c.rating)
Index("ix_reviews_publish_time", reviews_table.c.publish_time)
Index("ix_reviews_content_hash", reviews_table.c.content_hash)


# ---------------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------------


def get_engine(db_path: str | Path | None = None) -> Engine:
    """Return (and cache) the SQLAlchemy engine for reviews.db.

    Args:
        db_path: Explicit path to the SQLite file.  Defaults to
                 ``<project_root>/data/reviews.db``.
                 Can also be set via the environment variable
                 ``REVIEWS_DB_PATH``.

    Returns:
        A connected :class:`sqlalchemy.Engine` instance.
    """
    global _engine

    if _engine is not None:
        return _engine

    resolved = (
        Path(db_path)
        if db_path
        else Path(os.environ.get("REVIEWS_DB_PATH", str(_DEFAULT_DB_PATH)))
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
    """Create all tables, indexes, and constraints if they do not exist.

    Idempotent — safe to call on every application start-up.

    Args:
        db_path: Forwarded to :func:`get_engine`.
    """
    engine = get_engine(db_path)
    metadata.create_all(engine)


def get_connection(db_path: str | Path | None = None) -> Connection:
    """Return a raw SQLAlchemy 2.x :class:`~sqlalchemy.Connection`.

    Usage::

        with get_connection() as conn:
            rows = conn.execute(reviews_table.select()).mappings().all()

    Args:
        db_path: Forwarded to :func:`get_engine`.

    Returns:
        An open :class:`sqlalchemy.Connection`.
    """
    return get_engine(db_path).connect()


# ---------------------------------------------------------------------------
# Content-hash helper
# ---------------------------------------------------------------------------


def compute_content_hash(
    author_uri: str | None,
    rating: int,
    text: str | None,
    publish_time: str | None,
) -> str:
    """Compute a deterministic SHA-256 content hash for a review.

    The hash is used as the de-duplication key so that re-crawling the same
    review does not create duplicate rows.

    Args:
        author_uri: Unique author identifier returned by the Places API.
        rating:     Star rating (1-5).
        text:       Review body text (may be ``None``).
        publish_time: ISO-8601 publish timestamp.

    Returns:
        A 64-character lowercase hex digest.
    """
    payload = json.dumps(
        {
            "author_uri": author_uri or "",
            "rating": rating,
            "text": text or "",
            "publish_time": publish_time or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Convenience query helpers
# ---------------------------------------------------------------------------


def insert_review_if_new(record: dict, db_path: str | Path | None = None) -> bool:
    """Insert *record* only if its ``content_hash`` is not already present.

    Updates ``last_seen_at`` on the existing row when a duplicate is found.

    Args:
        record: Mapping of column name → value.  Must include ``content_hash``.
        db_path: Forwarded to :func:`get_engine`.

    Returns:
        ``True`` if a new row was inserted, ``False`` if it was a duplicate.

    Raises:
        ValueError: If ``content_hash`` is missing from *record*.
    """
    if "content_hash" not in record:
        raise ValueError("record must include 'content_hash'")

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    allowed_cols = {c.key for c in reviews_table.columns}
    clean = {k: v for k, v in record.items() if k in allowed_cols}

    with get_connection(db_path) as conn:
        stmt = (
            sqlite_insert(reviews_table)
            .values(**clean)
            .on_conflict_do_update(
                index_elements=["content_hash"],
                set_={"last_seen_at": clean.get("last_seen_at") or clean.get("first_seen_at")},
            )
        )
        result = conn.execute(stmt)
        conn.commit()
        # rowcount == 1 on insert, 0 on no-op update (SQLite returns 1 for
        # on_conflict_do_update even for updates; use lastrowid instead)
        return result.lastrowid is not None


def get_reviews_by_place_id(
    place_id: str, db_path: str | Path | None = None
) -> list[dict]:
    """Return all reviews for a given Google Place ID."""
    with get_connection(db_path) as conn:
        stmt = reviews_table.select().where(reviews_table.c.place_id == place_id)
        rows = conn.execute(stmt).mappings().all()
        return [dict(row) for row in rows]


def get_review_count_by_district(
    db_path: str | Path | None = None,
) -> list[dict]:
    """Return per-district review counts.

    Returns:
        List of ``{"district": str, "count": int}`` dicts sorted by count
        descending.
    """
    with get_connection(db_path) as conn:
        stmt = (
            select(
                reviews_table.c.district,
                func.count().label("count"),
            )
            .group_by(reviews_table.c.district)
            .order_by(func.count().desc())
        )
        rows = conn.execute(stmt).mappings().all()
        return [dict(row) for row in rows]


def get_rating_distribution(
    kindergarten_id: int, db_path: str | Path | None = None
) -> dict[int, int]:
    """Return the star-rating distribution for a kindergarten.

    Args:
        kindergarten_id: PK of the kindergarten.
        db_path: Forwarded to :func:`get_engine`.

    Returns:
        Dict mapping rating (1–5) to count, e.g. ``{1: 2, 2: 0, 3: 1, 4: 5, 5: 10}``.
    """
    with get_connection(db_path) as conn:
        stmt = (
            select(
                reviews_table.c.rating,
                func.count().label("count"),
            )
            .where(reviews_table.c.kindergarten_id == kindergarten_id)
            .group_by(reviews_table.c.rating)
        )
        rows = conn.execute(stmt).mappings().all()
        distribution = {r: 0 for r in range(1, 6)}
        for row in rows:
            distribution[row["rating"]] = row["count"]
        return distribution


# ---------------------------------------------------------------------------
# Module initialisation guard
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    create_tables()
    print("reviews.db — tables created successfully.")
