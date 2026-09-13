"""
kindergarten_service.py
-----------------------
CRUD operations for the ``official_kindergartens`` table stored in
``official_kindergartens.db``.

All database access is performed through the engine helper in
``database/kindergarten_db.py``.  The module gracefully falls back to a
direct SQLite connection via ``config/settings.py`` paths if the dedicated
engine module is not yet present (useful during early bootstrap).

Table schema (expected)
-----------------------
id                  INTEGER PRIMARY KEY AUTOINCREMENT
official_source_id  TEXT UNIQUE NOT NULL       -- from open-data source
official_name       TEXT
school_name         TEXT
kindergarten_type   TEXT
public_type         TEXT
district            TEXT
address             TEXT
phone               TEXT
principal           TEXT
approved_capacity   INTEGER
public_type_label   TEXT
matched             INTEGER DEFAULT 0          -- 0=not matched, 1=matched
created_at          TEXT
updated_at          TEXT
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine / connection resolution
# ---------------------------------------------------------------------------

def _get_db_path() -> str:
    """Return the filesystem path for official_kindergartens.db."""
    try:
        from config.settings import settings  # noqa: PLC0415

        return str(settings.kindergartens_db_path)
    except Exception:  # noqa: BLE001
        # Fallback: resolve relative to this file's location
        import os

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "data", "db", "official_kindergartens.db")


def _get_connection():
    """
    Return a live SQLAlchemy engine if ``database.kindergarten_db`` is
    available, otherwise fall back to a raw ``sqlite3`` connection via
    the path from settings.
    """
    try:
        from database.kindergarten_db import get_engine  # noqa: PLC0415

        return get_engine()
    except ImportError:
        pass

    # Bare sqlite3 fallback — callers must use the _sqlite_conn context manager.
    return None


@contextmanager
def _sqlite_conn() -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding a sqlite3 connection to the kindergartens DB."""
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


def insert_kindergarten(data: dict[str, Any]) -> int:
    """
    Insert a new kindergarten record and return its generated ``id``.

    Parameters
    ----------
    data:
        Dictionary of column-name → value pairs.  ``official_source_id`` is
        required.

    Returns
    -------
    int
        The ``rowid`` / ``id`` of the newly inserted row.

    Raises
    ------
    ValueError
        If ``official_source_id`` is missing from *data*.
    sqlite3.IntegrityError
        If a record with the same ``official_source_id`` already exists.
    """
    if not data.get("official_source_id"):
        raise ValueError("'official_source_id' is required to insert a kindergarten.")

    columns = list(data.keys())
    placeholders = ", ".join("?" * len(columns))
    col_clause = ", ".join(columns)
    sql = f"INSERT INTO official_kindergartens ({col_clause}) VALUES ({placeholders})"
    values = [data[c] for c in columns]

    with _sqlite_conn() as conn:
        cursor = conn.execute(sql, values)
        row_id = cursor.lastrowid

    logger.debug("Inserted kindergarten id=%d (source_id=%s)", row_id, data["official_source_id"])
    return row_id  # type: ignore[return-value]


def get_kindergarten_by_id(kindergarten_id: int) -> Optional[dict[str, Any]]:
    """
    Fetch a single kindergarten record by its primary key.

    Returns ``None`` if no record matches.
    """
    sql = "SELECT * FROM official_kindergartens WHERE id = ?"
    with _sqlite_conn() as conn:
        row = conn.execute(sql, (kindergarten_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def get_all_public_kindergartens(district: Optional[str] = None) -> list[dict[str, Any]]:
    """
    Return all kindergartens whose ``public_type_label`` or classification
    marks them as PUBLIC.

    Parameters
    ----------
    district:
        If provided, filter results to the given district (e.g. ``"板橋區"``).

    Returns
    -------
    list[dict]
        List of row dicts.
    """
    sql = "SELECT * FROM official_kindergartens WHERE kindergarten_type = '公立'"
    params: list[Any] = []

    if district:
        sql += " AND district = ?"
        params.append(district)

    sql += " ORDER BY district, official_name"

    with _sqlite_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_kindergarten_count(district: Optional[str] = None) -> int:
    """
    Return the total number of kindergarten records, optionally filtered
    by district.
    """
    sql = "SELECT COUNT(*) FROM official_kindergartens"
    params: list[Any] = []
    if district:
        sql += " WHERE district = ?"
        params.append(district)

    with _sqlite_conn() as conn:
        (count,) = conn.execute(sql, params).fetchone()
    return count


def update_kindergarten(kindergarten_id: int, data: dict[str, Any]) -> None:
    """
    Update fields for an existing kindergarten record.

    Parameters
    ----------
    kindergarten_id:
        Primary key of the record to update.
    data:
        Column → value pairs to update.  ``id`` and ``official_source_id``
        are silently excluded to prevent accidental key mutation.
    """
    # Exclude immutable keys
    safe_data = {k: v for k, v in data.items() if k not in ("id", "official_source_id")}
    if not safe_data:
        logger.debug("update_kindergarten called with no updatable fields; skipping.")
        return

    set_clause = ", ".join(f"{col} = ?" for col in safe_data)
    sql = f"UPDATE official_kindergartens SET {set_clause} WHERE id = ?"
    values = list(safe_data.values()) + [kindergarten_id]

    with _sqlite_conn() as conn:
        conn.execute(sql, values)
    logger.debug("Updated kindergarten id=%d, fields=%s", kindergarten_id, list(safe_data.keys()))


def kindergarten_exists(official_source_id: str) -> bool:
    """
    Return True if a kindergarten with the given ``official_source_id``
    already exists in the database.
    """
    sql = "SELECT 1 FROM official_kindergartens WHERE official_source_id = ? LIMIT 1"
    with _sqlite_conn() as conn:
        row = conn.execute(sql, (official_source_id,)).fetchone()
    return row is not None


def get_kindergartens_for_matching(
    district: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    Return PUBLIC kindergartens that have not yet been matched to a Google
    Places entry (``matched = 0``).

    Parameters
    ----------
    district:
        Optional district filter.
    limit:
        Maximum number of records to return.  ``None`` means no limit.

    Returns
    -------
    list[dict]
        Unmatched PUBLIC kindergarten records.
    """
    sql = (
        "SELECT * FROM official_kindergartens "
        "WHERE kindergarten_type = '公立' AND matched = 0"
    )
    params: list[Any] = []

    if district:
        sql += " AND district = ?"
        params.append(district)

    sql += " ORDER BY district, official_name"

    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    with _sqlite_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]
