import sqlite3
from app.config import DB_PATH

SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS kindergartens (
 id TEXT PRIMARY KEY, official_name TEXT NOT NULL, normalized_name TEXT NOT NULL,
 city TEXT NOT NULL, district TEXT, address TEXT, phone TEXT, source TEXT NOT NULL,
 updated_at TEXT NOT NULL, school_year TEXT, code TEXT, public_private TEXT,
 metadata TEXT NOT NULL DEFAULT '{}', UNIQUE(city, district, official_name)
);
CREATE TABLE IF NOT EXISTS items (
 id TEXT PRIMARY KEY, kindergarten_id TEXT REFERENCES kindergartens(id),
 kindergarten_name TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL, source_type TEXT NOT NULL,
 publisher TEXT, author TEXT, title TEXT, content TEXT, url TEXT NOT NULL UNIQUE,
 published_at TEXT, collected_at TEXT NOT NULL, query TEXT,
 risk_score INTEGER NOT NULL CHECK(risk_score BETWEEN 0 AND 100),
 verified INTEGER NOT NULL CHECK(verified IN (0,1)), normalized_hash TEXT NOT NULL UNIQUE,
 metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS risk_tags (
 item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
 tag TEXT NOT NULL, PRIMARY KEY(item_id, tag)
);
CREATE TABLE IF NOT EXISTS crawl_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
 source TEXT NOT NULL, status TEXT NOT NULL, items_found INTEGER NOT NULL DEFAULT 0,
 error TEXT, query TEXT, items_inserted INTEGER NOT NULL DEFAULT 0, scope TEXT
);
CREATE TABLE IF NOT EXISTS report_scope (
 id INTEGER PRIMARY KEY CHECK(id=1), city TEXT NOT NULL, district TEXT,
 kindergarten TEXT, requested_limit INTEGER NOT NULL, selected_ids TEXT NOT NULL,
 selected_count INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS schools_scope ON kindergartens(city, district);
CREATE INDEX IF NOT EXISTS items_attention ON items(risk_score DESC);
CREATE INDEX IF NOT EXISTS items_school ON items(kindergarten_id);
'''

def connect(path=DB_PATH):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript(SCHEMA)
    return conn
