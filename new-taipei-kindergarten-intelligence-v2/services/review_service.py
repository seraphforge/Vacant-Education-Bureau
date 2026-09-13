"""
review_service.py
-----------------
CRUD operations for the ``reviews`` table stored in ``reviews.db``.

Handles deduplication via ``content_hash``, rating validation, and
aggregated queries for rating distributions.

Table schema (expected)
-----------------------
id                  INTEGER PRIMARY KEY AUTOINCREMENT
kindergarten_id     INTEGER NOT NULL       -- FK → official_kindergartens.id
place_id            TEXT                   -- Google Place ID
reviewer_name       TEXT
rating              INTEGER                -- 1–5
review_text         TEXT
review_date         TEXT                   -- ISO date string
lang                TEXT DEFAULT 'zh-TW'
content_hash        TEXT UNIQUE NOT NULL   -- SHA-256 of (place_id + rating + review_text)
source              TEXT DEFAULT 'google'
district            TEXT
created_at          TEXT
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_RATINGS: frozenset[int] = frozenset(range(1, 6))  # {1, 2, 3, 4, 5}

# ---------------------------------------------------------------------------
# Engine / connection resolution
# ---------------------------------------------------------------------------


def _get_db_path() -> str:
    """Return the filesystem path for reviews.db."""
    try:
        from config.settings import settings  # noqa: PLC0415

        return str(settings.reviews_db_path)
    except Exception:  # noqa: BLE001
        import os

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "data", "db", "reviews.db")


@contextmanager
def _sqlite_conn() -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding a sqlite3 connection to the reviews DB."""
    path = _get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_rating(rating: Any) -> bool:
    """
    Return True if *rating* is an integer (or integer-equivalent) in the
    range 1–5 (inclusive).

    Accepts ``int`` and ``bool`` (which is a subclass of ``int`` in Python,
    but ``True``/``False`` are excluded because they are not meaningful star
    ratings).  Rejects strings, floats, ``None``, and any value outside 1–5.

    Google Maps reviews only use star ratings 1–5.
    """
    if rating is None:
        return False
    # Reject booleans — bool is a subclass of int in Python
    if isinstance(rating, bool):
        return False
    # Accept only true integers (not floats, not strings)
    if not isinstance(rating, int):
        return False
    return rating in VALID_RATINGS


def review_exists(content_hash: str) -> bool:
    """
    Return True if a review with the given ``content_hash`` already exists.

    Used for deduplication before inserting new reviews.
    """
    sql = "SELECT 1 FROM reviews WHERE content_hash = ? LIMIT 1"
    with _sqlite_conn() as conn:
        row = conn.execute(sql, (content_hash,)).fetchone()
    return row is not None


def insert_review(data: dict[str, Any]) -> Optional[int]:
    """
    Insert a new review record.

    Deduplication is performed via ``content_hash``.  If a review with the
    same hash already exists the insert is skipped and ``None`` is returned.

    Parameters
    ----------
    data:
        Column → value pairs.  Required fields:
        ``kindergarten_id``, ``content_hash``, ``rating``.

    Returns
    -------
    int
        The ``id`` of the newly inserted row.
    None
        If the review is a duplicate (``content_hash`` collision).

    Raises
    ------
    ValueError
        If required fields are missing or the rating is invalid.
    """
    # Validate required fields
    required = ("kindergarten_id", "content_hash", "rating")
    missing = [f for f in required if data.get(f) is None]
    if missing:
        raise ValueError(f"insert_review: missing required fields: {missing}")

    if not validate_rating(data["rating"]):
        # Try to coerce — e.g. if data came from a CSV as a string
        try:
            coerced = int(data["rating"])
            if coerced in VALID_RATINGS:
                data = {**data, "rating": coerced}
            else:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError(
                f"insert_review: invalid rating {data['rating']!r}. Must be integer 1–5."
            )

    # Duplicate check
    if review_exists(str(data["content_hash"])):
        logger.debug(
            "Skipping duplicate review (content_hash=%s, kindergarten_id=%s)",
            data["content_hash"],
            data["kindergarten_id"],
        )
        return None

    import datetime

    insert_data = {**data}
    insert_data.setdefault("source", "google")
    insert_data.setdefault("lang", "zh-TW")
    insert_data.setdefault(
        "created_at", datetime.datetime.utcnow().isoformat(timespec="seconds")
    )

    columns = list(insert_data.keys())
    placeholders = ", ".join("?" * len(columns))
    col_clause = ", ".join(columns)
    sql = f"INSERT INTO reviews ({col_clause}) VALUES ({placeholders})"
    values = [insert_data[c] for c in columns]

    try:
        with _sqlite_conn() as conn:
            cursor = conn.execute(sql, values)
            row_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        # Race condition — another process inserted the same hash.
        logger.debug("IntegrityError on insert (duplicate content_hash=%s).", data["content_hash"])
        return None

    logger.debug(
        "Inserted review id=%d for kindergarten_id=%d",
        row_id,
        data["kindergarten_id"],
    )
    return row_id  # type: ignore[return-value]


def get_reviews_by_kindergarten(kindergarten_id: int) -> list[dict[str, Any]]:
    """
    Return all reviews for the given kindergarten, ordered by review_date
    descending (newest first).
    """
    sql = (
        "SELECT * FROM reviews "
        "WHERE kindergarten_id = ? "
        "ORDER BY review_date DESC, id DESC"
    )
    with _sqlite_conn() as conn:
        rows = conn.execute(sql, (kindergarten_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_reviews_by_district(district: str) -> list[dict[str, Any]]:
    """
    Return all reviews for kindergartens in the given district, ordered by
    kindergarten_id then review_date descending.
    """
    sql = (
        "SELECT * FROM reviews "
        "WHERE district = ? "
        "ORDER BY kindergarten_id, review_date DESC, id DESC"
    )
    with _sqlite_conn() as conn:
        rows = conn.execute(sql, (district,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_review_count(
    kindergarten_id: Optional[int] = None,
    district: Optional[str] = None,
) -> int:
    """
    Return the number of reviews, optionally filtered by kindergarten and/or
    district.

    Parameters
    ----------
    kindergarten_id:
        Filter to reviews for this kindergarten only.
    district:
        Filter to reviews for kindergartens in this district only.

    Returns
    -------
    int
    """
    conditions: list[str] = []
    params: list[Any] = []

    if kindergarten_id is not None:
        conditions.append("kindergarten_id = ?")
        params.append(kindergarten_id)
    if district is not None:
        conditions.append("district = ?")
        params.append(district)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT COUNT(*) FROM reviews {where}"

    with _sqlite_conn() as conn:
        (count,) = conn.execute(sql, params).fetchone()
    return count


def get_rating_distribution(
    kindergarten_id: Optional[int] = None,
    district: Optional[str] = None,
) -> dict[int, int]:
    """
    Return a count of reviews per star rating (1–5).

    Parameters
    ----------
    kindergarten_id:
        Restrict to a single kindergarten.
    district:
        Restrict to a single district.

    Returns
    -------
    dict[int, int]
        Mapping of ``{rating: count}`` for ratings 1 through 5.
        Missing ratings will have a count of 0.

    Example
    -------
    >>> get_rating_distribution(kindergarten_id=42)
    {1: 0, 2: 1, 3: 3, 4: 12, 5: 28}
    """
    conditions: list[str] = []
    params: list[Any] = []

    if kindergarten_id is not None:
        conditions.append("kindergarten_id = ?")
        params.append(kindergarten_id)
    if district is not None:
        conditions.append("district = ?")
        params.append(district)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT rating, COUNT(*) AS cnt FROM reviews {where} GROUP BY rating"

    with _sqlite_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    # Initialise all ratings to 0, then fill in actual counts.
    distribution: dict[int, int] = {r: 0 for r in VALID_RATINGS}
    for row in rows:
        rating = row["rating"]
        if validate_rating(rating):
            distribution[int(rating)] = row["cnt"]
        else:
            logger.warning("Unexpected rating value %r in reviews table.", rating)

    return distribution
