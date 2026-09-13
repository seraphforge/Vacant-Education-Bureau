"""
place_service.py
----------------
CRUD operations for the ``place_matches`` table stored in
``google_places.db``.

Handles Google Places match records, including upsert semantics and loading
manual overrides from ``config/place_overrides.csv``.

Table schema (expected)
-----------------------
id                  INTEGER PRIMARY KEY AUTOINCREMENT
kindergarten_id     INTEGER UNIQUE NOT NULL   -- FK → official_kindergartens.id
place_id            TEXT                      -- Google Place ID
place_name          TEXT
place_address       TEXT
district            TEXT
lat                 REAL
lng                 REAL
match_score         REAL
match_method        TEXT                      -- e.g. "fuzzy", "override", "exact"
status              TEXT DEFAULT 'pending'    -- pending | matched | rejected | override
override_source     TEXT                      -- "csv" if from place_overrides.csv
created_at          TEXT
updated_at          TEXT
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine / connection resolution
# ---------------------------------------------------------------------------


def _get_db_path() -> str:
    """Return the filesystem path for google_places.db."""
    try:
        from config.settings import settings  # noqa: PLC0415

        return str(settings.places_db_path)
    except Exception:  # noqa: BLE001
        import os

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "data", "db", "google_places.db")


def _get_overrides_csv_path() -> Path:
    """Return the Path to config/place_overrides.csv."""
    try:
        from config.settings import settings  # noqa: PLC0415

        return Path(settings.place_overrides_csv)
    except Exception:  # noqa: BLE001
        import os

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return Path(base) / "config" / "place_overrides.csv"


@contextmanager
def _sqlite_conn() -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding a sqlite3 connection to the places DB."""
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


def upsert_place_match(data: dict[str, Any]) -> int:
    """
    Insert a new place match or update the existing record for the same
    ``kindergarten_id``.

    Parameters
    ----------
    data:
        Column → value pairs.  ``kindergarten_id`` is required.

    Returns
    -------
    int
        The ``id`` of the upserted row.

    Raises
    ------
    ValueError
        If ``kindergarten_id`` is missing from *data*.
    """
    if data.get("kindergarten_id") is None:
        raise ValueError("'kindergarten_id' is required to upsert a place match.")

    # Build INSERT OR REPLACE (SQLite upsert).
    # We explicitly set updated_at so we can track mutations.
    import datetime

    upsert_data = {**data}
    upsert_data.setdefault("status", "pending")
    upsert_data["updated_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
    if "created_at" not in upsert_data:
        upsert_data["created_at"] = upsert_data["updated_at"]

    columns = list(upsert_data.keys())
    placeholders = ", ".join("?" * len(columns))
    col_clause = ", ".join(columns)
    # INSERT OR REPLACE respects the UNIQUE constraint on kindergarten_id.
    sql = (
        f"INSERT INTO place_matches ({col_clause}) VALUES ({placeholders}) "
        f"ON CONFLICT(kindergarten_id) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in columns if c not in ("id", "created_at"))
    )
    values = [upsert_data[c] for c in columns]

    with _sqlite_conn() as conn:
        cursor = conn.execute(sql, values)
        # After upsert, retrieve the id.
        row_id = cursor.lastrowid
        if not row_id:
            row = conn.execute(
                "SELECT id FROM place_matches WHERE kindergarten_id = ?",
                (data["kindergarten_id"],),
            ).fetchone()
            row_id = row["id"] if row else 0

    logger.debug(
        "Upserted place match id=%d for kindergarten_id=%d",
        row_id,
        data["kindergarten_id"],
    )
    return row_id  # type: ignore[return-value]


def get_place_by_kindergarten_id(kindergarten_id: int) -> Optional[dict[str, Any]]:
    """
    Fetch the place match record for a given kindergarten.

    Returns ``None`` if no match exists yet.
    """
    sql = "SELECT * FROM place_matches WHERE kindergarten_id = ?"
    with _sqlite_conn() as conn:
        row = conn.execute(sql, (kindergarten_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_places_by_district(district: str) -> list[dict[str, Any]]:
    """
    Return all place match records for the given district.

    Parameters
    ----------
    district:
        District name, e.g. ``"板橋區"``.

    Returns
    -------
    list[dict]
    """
    sql = "SELECT * FROM place_matches WHERE district = ? ORDER BY place_name"
    with _sqlite_conn() as conn:
        rows = conn.execute(sql, (district,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_places_by_status(status: str) -> list[dict[str, Any]]:
    """
    Return all place match records with the given ``status``.

    Common status values: ``"pending"``, ``"matched"``, ``"rejected"``,
    ``"override"``.

    Parameters
    ----------
    status:
        Status string to filter on.

    Returns
    -------
    list[dict]
    """
    sql = "SELECT * FROM place_matches WHERE status = ? ORDER BY district, place_name"
    with _sqlite_conn() as conn:
        rows = conn.execute(sql, (status,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_matched_count(district: Optional[str] = None) -> int:
    """
    Return the number of place matches with status = 'matched' (or
    'override'), optionally filtered by district.
    """
    sql = "SELECT COUNT(*) FROM place_matches WHERE status IN ('matched', 'override')"
    params: list[Any] = []
    if district:
        sql += " AND district = ?"
        params.append(district)

    with _sqlite_conn() as conn:
        (count,) = conn.execute(sql, params).fetchone()
    return count


def place_match_exists(kindergarten_id: int) -> bool:
    """
    Return True if a place match record exists for the given kindergarten.
    """
    sql = "SELECT 1 FROM place_matches WHERE kindergarten_id = ? LIMIT 1"
    with _sqlite_conn() as conn:
        row = conn.execute(sql, (kindergarten_id,)).fetchone()
    return row is not None


def load_overrides() -> dict[int, dict[str, Any]]:
    """
    Load manual place-match overrides from ``config/place_overrides.csv``.

    The CSV is expected to have at minimum these columns:
        kindergarten_id, place_id, place_name, place_address, lat, lng

    Optional columns (will be included if present):
        district, match_score, notes

    Returns
    -------
    dict[int, dict]
        Mapping of ``kindergarten_id`` (int) → override data dict.
        Returns an empty dict if the CSV does not exist or is malformed.
    """
    csv_path = _get_overrides_csv_path()

    if not csv_path.exists():
        logger.info("place_overrides.csv not found at %s; no overrides loaded.", csv_path)
        return {}

    overrides: dict[int, dict[str, Any]] = {}
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for line_num, row in enumerate(reader, start=2):  # header is line 1
                raw_id = row.get("kindergarten_id", "").strip()
                if not raw_id:
                    logger.warning(
                        "place_overrides.csv line %d: missing kindergarten_id, skipping.",
                        line_num,
                    )
                    continue
                try:
                    kid_id = int(raw_id)
                except ValueError:
                    logger.warning(
                        "place_overrides.csv line %d: non-integer kindergarten_id '%s', skipping.",
                        line_num,
                        raw_id,
                    )
                    continue

                # Coerce numeric fields
                override: dict[str, Any] = {
                    k: v.strip() for k, v in row.items() if v is not None
                }
                override["kindergarten_id"] = kid_id
                override["status"] = "override"
                override["override_source"] = "csv"

                for float_field in ("lat", "lng", "match_score"):
                    if float_field in override and override[float_field]:
                        try:
                            override[float_field] = float(override[float_field])
                        except ValueError:
                            override.pop(float_field, None)

                overrides[kid_id] = override

        logger.info("Loaded %d place overrides from %s.", len(overrides), csv_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load place_overrides.csv from %s: %s", csv_path, exc)

    return overrides
