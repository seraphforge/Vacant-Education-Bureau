"""
tests/test_master_builder.py
-----------------------------
Tests for services.master_builder.MasterBuilder.

Because the database modules use module-level SQLAlchemy engine singletons,
each test uses its own set of temp DB paths and creates fresh SQLAlchemy
engines for seeding data — avoiding collisions with any real databases.
"""

import sqlite3
import pytest
from pathlib import Path
from sqlalchemy import create_engine, text, MetaData


# ---------------------------------------------------------------------------
# Helper to build a minimal test database using raw sqlite3 (no singleton)
# ---------------------------------------------------------------------------

def _create_official_db(db_path: Path) -> None:
    """Create official_kindergartens.db schema with one test row."""
    conn = sqlite3.connect(str(db_path))
    conn.execute('''
        CREATE TABLE kindergartens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            official_source_id TEXT UNIQUE,
            official_name TEXT NOT NULL,
            school_name TEXT,
            kindergarten_name TEXT,
            kindergarten_type TEXT,
            public_type TEXT,
            district TEXT NOT NULL,
            address TEXT,
            postal_code TEXT,
            phone TEXT,
            latitude REAL,
            longitude REAL,
            official_source TEXT,
            official_source_url TEXT,
            is_public INTEGER DEFAULT 1,
            status TEXT DEFAULT 'ACTIVE',
            created_at TEXT,
            updated_at TEXT
        )
    ''')
    conn.execute('''
        INSERT INTO kindergartens
            (official_source_id, official_name, district, is_public, status)
        VALUES
            ('SRC_001', '新北市立汐止幼兒園', '汐止區', 1, 'ACTIVE')
    ''')
    conn.commit()
    conn.close()


def _create_empty_official_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute('''
        CREATE TABLE kindergartens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            official_source_id TEXT UNIQUE,
            official_name TEXT NOT NULL,
            school_name TEXT,
            kindergarten_name TEXT,
            kindergarten_type TEXT,
            public_type TEXT,
            district TEXT NOT NULL,
            address TEXT,
            postal_code TEXT,
            phone TEXT,
            latitude REAL,
            longitude REAL,
            official_source TEXT,
            official_source_url TEXT,
            is_public INTEGER DEFAULT 1,
            status TEXT DEFAULT 'ACTIVE',
            created_at TEXT,
            updated_at TEXT
        )
    ''')
    conn.commit()
    conn.close()


def _create_places_db(db_path: Path, kindergarten_id: int = 1) -> None:
    """Create google_places.db schema with one valid and (optionally) one invalid row."""
    conn = sqlite3.connect(str(db_path))
    conn.execute('''
        CREATE TABLE place_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kindergarten_id INTEGER NOT NULL,
            official_name TEXT,
            district TEXT,
            official_address TEXT,
            google_place_id TEXT UNIQUE,
            google_name TEXT,
            google_address TEXT,
            google_latitude REAL,
            google_longitude REAL,
            google_rating REAL,
            google_user_rating_count INTEGER,
            google_maps_uri TEXT,
            business_status TEXT,
            match_score REAL,
            name_score REAL,
            address_score REAL,
            geo_score REAL,
            match_status TEXT DEFAULT 'PENDING',
            match_method TEXT,
            raw_json TEXT,
            collection_scope TEXT DEFAULT 'GOOGLE_PLACES_API_PARTIAL',
            first_seen_at TEXT,
            last_seen_at TEXT
        )
    ''')
    conn.execute('''
        INSERT INTO place_matches
            (kindergarten_id, official_name, district, google_place_id, match_status)
        VALUES
            (?, '新北市立汐止幼兒園', '汐止區', 'ChIJvalid001', 'AUTO_ACCEPT')
    ''', (kindergarten_id,))
    conn.commit()
    conn.close()


def _create_places_db_with_orphan(db_path: Path) -> None:
    """Create places DB with a row whose kindergarten_id doesn't exist."""
    conn = sqlite3.connect(str(db_path))
    conn.execute('''
        CREATE TABLE place_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kindergarten_id INTEGER NOT NULL,
            official_name TEXT,
            district TEXT,
            official_address TEXT,
            google_place_id TEXT,
            google_name TEXT,
            google_address TEXT,
            google_latitude REAL,
            google_longitude REAL,
            google_rating REAL,
            google_user_rating_count INTEGER,
            google_maps_uri TEXT,
            business_status TEXT,
            match_score REAL,
            name_score REAL,
            address_score REAL,
            geo_score REAL,
            match_status TEXT DEFAULT 'PENDING',
            match_method TEXT,
            raw_json TEXT,
            collection_scope TEXT DEFAULT 'GOOGLE_PLACES_API_PARTIAL',
            first_seen_at TEXT,
            last_seen_at TEXT
        )
    ''')
    # orphan: kindergarten_id=9999 does not exist
    conn.execute('''
        INSERT INTO place_matches (kindergarten_id, google_place_id, match_status)
        VALUES (9999, 'ChIJorphan', 'AUTO_ACCEPT')
    ''')
    conn.commit()
    conn.close()


def _create_reviews_db(db_path: Path, kindergarten_id: int = 1) -> None:
    """Create reviews.db schema with one valid row."""
    conn = sqlite3.connect(str(db_path))
    conn.execute('''
        CREATE TABLE reviews (
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
    conn.execute('''
        INSERT INTO reviews
            (kindergarten_id, district, place_id, rating, content_hash)
        VALUES
            (?, '汐止區', 'ChIJvalid001', 5, 'hash_abc123')
    ''', (kindergarten_id,))
    conn.commit()
    conn.close()


def _create_reviews_db_orphan(db_path: Path) -> None:
    """Create reviews.db with an orphan review (invalid kindergarten_id)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute('''
        CREATE TABLE reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kindergarten_id INTEGER NOT NULL,
            district TEXT NOT NULL,
            place_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            source TEXT DEFAULT 'GOOGLE_PLACES_API'
        )
    ''')
    conn.execute('''
        INSERT INTO reviews (kindergarten_id, district, place_id, rating, content_hash)
        VALUES (9999, '汐止區', 'ChIJorphan', 4, 'hash_orphan_xyz')
    ''')
    conn.commit()
    conn.close()


def _create_empty_db(db_path: Path, table_name: str, schema: str) -> None:
    """Generic helper to create an empty table."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(schema)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_paths(tmp_path):
    """Return a dict of temp db paths for a full MasterBuilder scenario."""
    return {
        "official": tmp_path / "official.db",
        "places": tmp_path / "places.db",
        "reviews": tmp_path / "reviews.db",
        "master": tmp_path / "master.db",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMasterBuilderBuild:

    def test_build_creates_master_db(self, db_paths):
        """After build(), master DB file exists with expected tables."""
        _create_official_db(db_paths["official"])
        # Create empty places and reviews DBs
        _create_places_db(db_paths["places"], kindergarten_id=1)
        _create_reviews_db(db_paths["reviews"], kindergarten_id=1)

        from services.master_builder import MasterBuilder
        builder = MasterBuilder(
            official_db_path=db_paths["official"],
            places_db_path=db_paths["places"],
            reviews_db_path=db_paths["reviews"],
            master_db_path=db_paths["master"],
        )
        builder.build()

        assert db_paths["master"].exists()

        # Verify tables were created
        conn = sqlite3.connect(str(db_paths["master"]))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "kindergartens" in tables
        assert "places" in tables
        assert "reviews" in tables

    def test_sync_kindergartens(self, db_paths):
        """Kindergartens from official_db appear in master_db after build()."""
        _create_official_db(db_paths["official"])
        _create_places_db(db_paths["places"], kindergarten_id=1)
        _create_reviews_db(db_paths["reviews"], kindergarten_id=1)

        from services.master_builder import MasterBuilder
        builder = MasterBuilder(
            official_db_path=db_paths["official"],
            places_db_path=db_paths["places"],
            reviews_db_path=db_paths["reviews"],
            master_db_path=db_paths["master"],
        )
        builder.build()

        conn = sqlite3.connect(str(db_paths["master"]))
        rows = conn.execute("SELECT official_name FROM kindergartens").fetchall()
        conn.close()

        names = [r[0] for r in rows]
        assert "新北市立汐止幼兒園" in names

    def test_valid_place_synced(self, db_paths):
        """A place with a valid kindergarten_id FK is synced to master_db."""
        _create_official_db(db_paths["official"])
        _create_places_db(db_paths["places"], kindergarten_id=1)
        _create_reviews_db(db_paths["reviews"], kindergarten_id=1)

        from services.master_builder import MasterBuilder
        builder = MasterBuilder(
            official_db_path=db_paths["official"],
            places_db_path=db_paths["places"],
            reviews_db_path=db_paths["reviews"],
            master_db_path=db_paths["master"],
        )
        builder.build()

        conn = sqlite3.connect(str(db_paths["master"]))
        places = conn.execute("SELECT google_place_id FROM places").fetchall()
        conn.close()

        place_ids = [r[0] for r in places]
        assert "ChIJvalid001" in place_ids

    def test_valid_review_synced(self, db_paths):
        """A review with a valid kindergarten_id FK is synced to master_db."""
        _create_official_db(db_paths["official"])
        _create_places_db(db_paths["places"], kindergarten_id=1)
        _create_reviews_db(db_paths["reviews"], kindergarten_id=1)

        from services.master_builder import MasterBuilder
        builder = MasterBuilder(
            official_db_path=db_paths["official"],
            places_db_path=db_paths["places"],
            reviews_db_path=db_paths["reviews"],
            master_db_path=db_paths["master"],
        )
        builder.build()

        conn = sqlite3.connect(str(db_paths["master"]))
        reviews = conn.execute("SELECT content_hash FROM reviews").fetchall()
        conn.close()

        hashes = [r[0] for r in reviews]
        assert "hash_abc123" in hashes

    def test_fk_validation_rejects_orphan_places(self, db_paths):
        """A place_match with non-existent kindergarten_id is rejected (not in master)."""
        _create_official_db(db_paths["official"])
        _create_places_db_with_orphan(db_paths["places"])
        _create_reviews_db(db_paths["reviews"], kindergarten_id=1)

        from services.master_builder import MasterBuilder
        builder = MasterBuilder(
            official_db_path=db_paths["official"],
            places_db_path=db_paths["places"],
            reviews_db_path=db_paths["reviews"],
            master_db_path=db_paths["master"],
        )
        builder.build()

        conn = sqlite3.connect(str(db_paths["master"]))
        places = conn.execute("SELECT google_place_id FROM places").fetchall()
        conn.close()

        place_ids = [r[0] for r in places]
        # Orphan place should NOT be in master
        assert "ChIJorphan" not in place_ids

    def test_fk_validation_rejects_orphan_reviews(self, db_paths):
        """A review with non-existent kindergarten_id is rejected (not in master)."""
        _create_official_db(db_paths["official"])
        _create_places_db(db_paths["places"], kindergarten_id=1)
        _create_reviews_db_orphan(db_paths["reviews"])

        from services.master_builder import MasterBuilder
        builder = MasterBuilder(
            official_db_path=db_paths["official"],
            places_db_path=db_paths["places"],
            reviews_db_path=db_paths["reviews"],
            master_db_path=db_paths["master"],
        )
        builder.build()

        conn = sqlite3.connect(str(db_paths["master"]))
        reviews = conn.execute("SELECT content_hash FROM reviews").fetchall()
        conn.close()

        hashes = [r[0] for r in reviews]
        assert "hash_orphan_xyz" not in hashes

    def test_build_returns_summary_dict(self, db_paths):
        """build() returns a dict with expected keys and integer counts."""
        _create_official_db(db_paths["official"])
        _create_places_db(db_paths["places"], kindergarten_id=1)
        _create_reviews_db(db_paths["reviews"], kindergarten_id=1)

        from services.master_builder import MasterBuilder
        builder = MasterBuilder(
            official_db_path=db_paths["official"],
            places_db_path=db_paths["places"],
            reviews_db_path=db_paths["reviews"],
            master_db_path=db_paths["master"],
        )
        summary = builder.build()

        assert isinstance(summary, dict)
        assert "kindergartens" in summary
        assert "places" in summary
        assert "reviews" in summary
        assert "fk_errors" in summary
        assert "build_time" in summary

        assert isinstance(summary["kindergartens"], int)
        assert isinstance(summary["places"], int)
        assert isinstance(summary["reviews"], int)
        assert isinstance(summary["fk_errors"], int)
