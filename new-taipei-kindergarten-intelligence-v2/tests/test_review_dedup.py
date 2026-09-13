"""
tests/test_review_dedup.py
---------------------------
Tests for review deduplication:
  - utils.hashing.compute_review_hash
  - services.review_service.validate_rating
  - Insert deduplication using a temp SQLite DB (bypassing the module-level
    singleton connection so tests remain isolated and do not hit real DBs).
"""

import sqlite3
import pytest
from pathlib import Path

from utils.hashing import compute_review_hash
from services.review_service import validate_rating


# ---------------------------------------------------------------------------
# Helpers — build a minimal in-memory / temp DB for insert tests
# ---------------------------------------------------------------------------

def _create_reviews_table(conn: sqlite3.Connection) -> None:
    """Create the reviews table in the given sqlite3 connection."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kindergarten_id INTEGER NOT NULL,
            district TEXT NOT NULL,
            place_id TEXT NOT NULL,
            review_resource_name TEXT,
            author_display_name TEXT,
            author_uri TEXT,
            author_photo_uri TEXT,
            rating INTEGER NOT NULL,
            text TEXT,
            original_text TEXT,
            text_language TEXT,
            original_language TEXT,
            publish_time TEXT,
            relative_publish_time TEXT,
            review_uri TEXT,
            source TEXT DEFAULT 'GOOGLE_PLACES_API',
            content_hash TEXT NOT NULL UNIQUE,
            first_seen_at TEXT,
            last_seen_at TEXT,
            raw_json TEXT
        )
    ''')
    conn.commit()


def _insert_review(conn: sqlite3.Connection, data: dict) -> int | None:
    """
    Insert a review into the temp DB if content_hash is not yet present.
    Returns the row id on success, None on duplicate.
    """
    cursor = conn.execute(
        "SELECT 1 FROM reviews WHERE content_hash = ? LIMIT 1",
        (data["content_hash"],),
    )
    if cursor.fetchone() is not None:
        return None  # duplicate — skip

    columns = list(data.keys())
    placeholders = ", ".join("?" * len(columns))
    col_clause = ", ".join(columns)
    sql = f"INSERT INTO reviews ({col_clause}) VALUES ({placeholders})"
    values = [data[c] for c in columns]
    cursor = conn.execute(sql, values)
    conn.commit()
    return cursor.lastrowid


def _make_review_data(
    *,
    kindergarten_id: int = 1,
    district: str = "汐止區",
    place_id: str = "ChIJtest",
    author_display_name: str = "王小明",
    rating: int = 5,
    text: str = "很好",
    original_text: str = "很好",
    publish_time: str = "2024-01-01T00:00:00Z",
    content_hash: str | None = None,
) -> dict:
    """Build a minimal review data dict, computing hash if not provided."""
    if content_hash is None:
        content_hash = compute_review_hash(
            place_id=place_id,
            author_display_name=author_display_name,
            rating=rating,
            original_text=original_text,
            publish_time=publish_time,
        )
    return {
        "kindergarten_id": kindergarten_id,
        "district": district,
        "place_id": place_id,
        "author_display_name": author_display_name,
        "rating": rating,
        "text": text,
        "original_text": original_text,
        "publish_time": publish_time,
        "content_hash": content_hash,
    }


# ---------------------------------------------------------------------------
# Hash tests
# ---------------------------------------------------------------------------

class TestComputeReviewHash:
    """Tests for utils.hashing.compute_review_hash()."""

    def test_same_review_same_hash(self):
        """Identical inputs → identical hash."""
        h1 = compute_review_hash("ChIJ1", "王小明", 5, "很好", "2024-01-01T00:00:00Z")
        h2 = compute_review_hash("ChIJ1", "王小明", 5, "很好", "2024-01-01T00:00:00Z")
        assert h1 == h2

    def test_different_author_different_hash(self):
        """Changing author_display_name → different hash."""
        h1 = compute_review_hash("ChIJ1", "王小明", 5, "很好", "2024-01-01T00:00:00Z")
        h2 = compute_review_hash("ChIJ1", "李大華", 5, "很好", "2024-01-01T00:00:00Z")
        assert h1 != h2

    def test_different_rating_different_hash(self):
        """Changing rating → different hash."""
        h1 = compute_review_hash("ChIJ1", "王小明", 5, "很好", "2024-01-01T00:00:00Z")
        h2 = compute_review_hash("ChIJ1", "王小明", 4, "很好", "2024-01-01T00:00:00Z")
        assert h1 != h2

    def test_different_text_different_hash(self):
        """Changing original_text → different hash."""
        h1 = compute_review_hash("ChIJ1", "王小明", 5, "很好", "2024-01-01T00:00:00Z")
        h2 = compute_review_hash("ChIJ1", "王小明", 5, "非常好", "2024-01-01T00:00:00Z")
        assert h1 != h2

    def test_hash_with_none_values(self):
        """None values are handled gracefully — no exception raised."""
        h = compute_review_hash(
            place_id="ChIJ1",
            author_display_name="王小明",
            rating=3,
            original_text=None,
            publish_time=None,
        )
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest is 64 chars

    def test_hash_returns_64_char_hex(self):
        """Hash output is a 64-character hex string (SHA-256)."""
        h = compute_review_hash("ChIJ1", "王小明", 5, "很好", "2024-01-01T00:00:00Z")
        assert len(h) == 64
        int(h, 16)  # must be valid hex — raises ValueError if not

    def test_all_none_does_not_raise(self):
        """All None inputs → produces a valid (non-crashing) hash."""
        h = compute_review_hash(None, None, None, None, None)
        assert isinstance(h, str)


# ---------------------------------------------------------------------------
# Insert dedup tests
# ---------------------------------------------------------------------------

class TestInsertReviewDedup:
    """Tests for insert-level deduplication using a temp SQLite DB."""

    @pytest.fixture
    def db_conn(self, tmp_path):
        """Provide a fresh in-memory SQLite connection with the reviews table."""
        db_path = tmp_path / "test_dedup_reviews.db"
        conn = sqlite3.connect(str(db_path))
        _create_reviews_table(conn)
        yield conn
        conn.close()

    def test_insert_review_dedup(self, db_conn):
        """Inserting the same review twice → second insert returns None."""
        data = _make_review_data()
        first_id = _insert_review(db_conn, data)
        second_id = _insert_review(db_conn, data)

        assert first_id is not None
        assert second_id is None  # duplicate skipped

    def test_insert_different_reviews(self, db_conn):
        """Two reviews with different hashes are both inserted."""
        data1 = _make_review_data(author_display_name="王小明")
        data2 = _make_review_data(author_display_name="李大華")

        id1 = _insert_review(db_conn, data1)
        id2 = _insert_review(db_conn, data2)

        assert id1 is not None
        assert id2 is not None
        assert id1 != id2

        # Verify both rows are in the DB
        count = db_conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        assert count == 2

    def test_only_one_row_after_duplicate_insert(self, db_conn):
        """After inserting the same review twice, there is exactly 1 row."""
        data = _make_review_data(rating=4, author_display_name="測試者")
        _insert_review(db_conn, data)
        _insert_review(db_conn, data)

        count = db_conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# Rating validation tests
# ---------------------------------------------------------------------------

class TestValidateRating:
    """Tests for services.review_service.validate_rating()."""

    @pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
    def test_valid_ratings(self, rating):
        """Ratings 1–5 are valid."""
        assert validate_rating(rating) is True

    @pytest.mark.parametrize("rating", [0, 6, -1, 100, -5])
    def test_invalid_integer_ratings(self, rating):
        """Integers outside 1–5 are invalid."""
        assert validate_rating(rating) is False

    def test_none_is_invalid(self):
        """None is not a valid rating."""
        assert validate_rating(None) is False

    def test_float_is_invalid(self):
        """Float (e.g. 4.5) is not a valid rating — must be integer."""
        assert validate_rating(4.5) is False

    def test_string_is_invalid(self):
        """String representation of a number is not valid."""
        assert validate_rating("5") is False

    def test_bool_is_invalid(self):
        """bool is a subclass of int in Python, but True/False are not star ratings."""
        assert validate_rating(True) is False
        assert validate_rating(False) is False
