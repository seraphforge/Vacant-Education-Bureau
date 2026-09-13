import pytest
import sqlite3
import tempfile
import os
from pathlib import Path

@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path

@pytest.fixture
def temp_official_db(tmp_path):
    db_path = tmp_path / "test_official.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute('''
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
        )
    ''')
    conn.commit()
    conn.close()
    return db_path

@pytest.fixture
def temp_places_db(tmp_path):
    db_path = tmp_path / "test_places.db"
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
    conn.commit()
    conn.close()
    return db_path

@pytest.fixture
def temp_reviews_db(tmp_path):
    db_path = tmp_path / "test_reviews.db"
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
    conn.commit()
    conn.close()
    return db_path
