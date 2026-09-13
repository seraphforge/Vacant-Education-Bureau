"""
tests/test_export.py
---------------------
Tests for services.export_service.ExportService.

Each test creates a minimal master DB via sqlite3, then calls ExportService
with explicit temp paths so it doesn't touch any real files.
"""

import csv
import json
import sqlite3
import pytest
from pathlib import Path

from config.new_taipei_districts import NEW_TAIPEI_DISTRICTS


# ---------------------------------------------------------------------------
# Helper — create a minimal master DB for export tests
# ---------------------------------------------------------------------------

def _create_master_db(db_path: Path) -> None:
    """Create master DB tables with minimal test data."""
    conn = sqlite3.connect(str(db_path))

    conn.executescript('''
        CREATE TABLE kindergartens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            official_source_id TEXT,
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
        );
        CREATE TABLE places (
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
        );
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
        );
        CREATE TABLE district_stats (
            district TEXT PRIMARY KEY,
            public_kindergarten_count INTEGER DEFAULT 0,
            matched_place_count INTEGER DEFAULT 0,
            unmatched_place_count INTEGER DEFAULT 0,
            total_google_review_count INTEGER DEFAULT 0,
            collected_review_count INTEGER DEFAULT 0,
            avg_google_rating REAL,
            avg_collected_rating REAL,
            rating_1_count INTEGER DEFAULT 0,
            rating_2_count INTEGER DEFAULT 0,
            rating_3_count INTEGER DEFAULT 0,
            rating_4_count INTEGER DEFAULT 0,
            rating_5_count INTEGER DEFAULT 0,
            last_sync_at TEXT
        );
        CREATE TABLE kindergarten_stats (
            kindergarten_id INTEGER PRIMARY KEY,
            official_name TEXT,
            district TEXT,
            place_id TEXT,
            google_rating REAL,
            google_review_count INTEGER DEFAULT 0,
            collected_review_count INTEGER DEFAULT 0,
            rating_1_count INTEGER DEFAULT 0,
            rating_2_count INTEGER DEFAULT 0,
            rating_3_count INTEGER DEFAULT 0,
            rating_4_count INTEGER DEFAULT 0,
            rating_5_count INTEGER DEFAULT 0,
            avg_collected_rating REAL,
            latest_review_time TEXT,
            oldest_review_time TEXT,
            last_sync_at TEXT
        );
        CREATE TABLE crawl_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawler TEXT,
            district TEXT,
            started_at TEXT,
            finished_at TEXT,
            total INTEGER DEFAULT 0,
            success INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            status TEXT DEFAULT 'PENDING',
            error TEXT
        );
        CREATE TABLE data_quality_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            kindergarten_id INTEGER,
            district TEXT,
            description TEXT,
            raw_value TEXT,
            created_at TEXT,
            resolved_at TEXT,
            resolved INTEGER DEFAULT 0
        );
    ''')

    # Insert one test kindergarten in 汐止區
    conn.execute('''
        INSERT INTO kindergartens
            (official_source_id, official_name, district, is_public, status)
        VALUES
            ('SRC_EXPORT_001', '新北市立汐止幼兒園', '汐止區', 1, 'ACTIVE')
    ''')

    # Insert one test place
    conn.execute('''
        INSERT INTO places
            (kindergarten_id, official_name, district, google_place_id, match_status)
        VALUES
            (1, '新北市立汐止幼兒園', '汐止區', 'ChIJexport001', 'AUTO_ACCEPT')
    ''')

    # Insert one test review
    conn.execute('''
        INSERT INTO reviews
            (kindergarten_id, district, place_id, rating, content_hash)
        VALUES
            (1, '汐止區', 'ChIJexport001', 5, 'export_hash_001')
    ''')

    conn.commit()
    conn.close()


def _create_empty_master_db(db_path: Path) -> None:
    """Create master DB with all tables but no data."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript('''
        CREATE TABLE kindergartens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            official_source_id TEXT,
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
        );
        CREATE TABLE places (
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
        );
        CREATE TABLE reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kindergarten_id INTEGER NOT NULL,
            district TEXT NOT NULL,
            place_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            source TEXT DEFAULT 'GOOGLE_PLACES_API'
        );
        CREATE TABLE district_stats (
            district TEXT PRIMARY KEY,
            public_kindergarten_count INTEGER DEFAULT 0,
            matched_place_count INTEGER DEFAULT 0,
            unmatched_place_count INTEGER DEFAULT 0,
            total_google_review_count INTEGER DEFAULT 0,
            collected_review_count INTEGER DEFAULT 0,
            avg_google_rating REAL,
            avg_collected_rating REAL,
            rating_1_count INTEGER DEFAULT 0,
            rating_2_count INTEGER DEFAULT 0,
            rating_3_count INTEGER DEFAULT 0,
            rating_4_count INTEGER DEFAULT 0,
            rating_5_count INTEGER DEFAULT 0,
            last_sync_at TEXT
        );
        CREATE TABLE kindergarten_stats (
            kindergarten_id INTEGER PRIMARY KEY,
            official_name TEXT,
            district TEXT
        );
        CREATE TABLE crawl_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT DEFAULT 'PENDING'
        );
        CREATE TABLE data_quality_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            resolved INTEGER DEFAULT 0
        );
    ''')
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def export_paths(tmp_path):
    master_db = tmp_path / "master.db"
    exports_dir = tmp_path / "exports"
    return {"master_db": master_db, "exports_dir": exports_dir}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExportService:

    def test_export_creates_output_dirs(self, export_paths):
        """After export_all(), exports/all/ directory exists."""
        _create_master_db(export_paths["master_db"])

        from services.export_service import ExportService
        svc = ExportService(
            master_db_path=export_paths["master_db"],
            exports_dir=export_paths["exports_dir"],
        )
        svc.export_all()

        all_dir = export_paths["exports_dir"] / "all"
        assert all_dir.exists(), "exports/all/ directory should have been created"

    def test_export_all_creates_csv_files(self, export_paths):
        """public_kindergartens.csv, google_places.csv, reviews.csv, district_stats.csv."""
        _create_master_db(export_paths["master_db"])

        from services.export_service import ExportService
        svc = ExportService(
            master_db_path=export_paths["master_db"],
            exports_dir=export_paths["exports_dir"],
        )
        svc.export_all()

        all_dir = export_paths["exports_dir"] / "all"
        assert (all_dir / "public_kindergartens.csv").exists()
        assert (all_dir / "google_places.csv").exists()
        assert (all_dir / "reviews.csv").exists()
        assert (all_dir / "district_stats.csv").exists()

    def test_export_creates_jsonl(self, export_paths):
        """reviews.jsonl is created in exports/all/."""
        _create_master_db(export_paths["master_db"])

        from services.export_service import ExportService
        svc = ExportService(
            master_db_path=export_paths["master_db"],
            exports_dir=export_paths["exports_dir"],
        )
        svc.export_all()

        assert (export_paths["exports_dir"] / "all" / "reviews.jsonl").exists()

    def test_export_by_district_all_29(self, export_paths):
        """All 29 district subdirs are created under exports/by_district/."""
        _create_master_db(export_paths["master_db"])

        from services.export_service import ExportService
        svc = ExportService(
            master_db_path=export_paths["master_db"],
            exports_dir=export_paths["exports_dir"],
        )
        svc.export_all()

        by_district_dir = export_paths["exports_dir"] / "by_district"
        assert by_district_dir.exists()

        for district in NEW_TAIPEI_DISTRICTS:
            dist_dir = by_district_dir / district
            assert dist_dir.exists(), f"District directory '{district}' should exist"

    def test_export_empty_district_has_stats_json(self, export_paths):
        """Even a district with no data gets a stats.json with zeros."""
        _create_master_db(export_paths["master_db"])

        from services.export_service import ExportService
        svc = ExportService(
            master_db_path=export_paths["master_db"],
            exports_dir=export_paths["exports_dir"],
        )
        svc.export_all()

        # 板橋區 has no data in our test DB
        stats_path = export_paths["exports_dir"] / "by_district" / "板橋區" / "stats.json"
        assert stats_path.exists(), "stats.json should exist even for empty district"

        with stats_path.open(encoding="utf-8") as f:
            stats = json.load(f)

        assert stats.get("public_kindergarten_count", -1) == 0
        assert stats.get("collected_review_count", -1) == 0

    def test_district_stats_json_format(self, export_paths):
        """stats.json has all required keys."""
        _create_master_db(export_paths["master_db"])

        from services.export_service import ExportService
        svc = ExportService(
            master_db_path=export_paths["master_db"],
            exports_dir=export_paths["exports_dir"],
        )
        svc.export_all()

        # Check the district that HAS data
        stats_path = (
            export_paths["exports_dir"] / "by_district" / "汐止區" / "stats.json"
        )
        assert stats_path.exists()

        with stats_path.open(encoding="utf-8") as f:
            stats = json.load(f)

        required_keys = {
            "district",
            "public_kindergarten_count",
            "matched_place_count",
            "collected_review_count",
        }
        for key in required_keys:
            assert key in stats, f"stats.json missing required key: {key}"

    def test_public_kindergartens_csv_has_data(self, export_paths):
        """public_kindergartens.csv contains the test kindergarten row."""
        _create_master_db(export_paths["master_db"])

        from services.export_service import ExportService
        svc = ExportService(
            master_db_path=export_paths["master_db"],
            exports_dir=export_paths["exports_dir"],
        )
        svc.export_all()

        csv_path = export_paths["exports_dir"] / "all" / "public_kindergartens.csv"
        with csv_path.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        names = [r.get("official_name") for r in rows]
        assert "新北市立汐止幼兒園" in names

    def test_reviews_csv_has_header(self, export_paths):
        """reviews.csv has a proper CSV header row."""
        _create_master_db(export_paths["master_db"])

        from services.export_service import ExportService
        svc = ExportService(
            master_db_path=export_paths["master_db"],
            exports_dir=export_paths["exports_dir"],
        )
        svc.export_all()

        csv_path = export_paths["exports_dir"] / "all" / "reviews.csv"
        with csv_path.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames

        assert fieldnames is not None
        assert "kindergarten_id" in fieldnames
        assert "rating" in fieldnames
        assert "content_hash" in fieldnames
