"""
database/master_db.py

Manages kindergarten_reviews_master.db — a consolidated, single-file view of
all data in the project. This database is the primary source for analytics,
reporting, and the frontend API.

Tables
------
* kindergartens       — mirrors official_kindergartens.db
* places              — mirrors google_places.db (place_matches)
* reviews             — mirrors reviews.db
* district_stats      — aggregated per-district statistics
* kindergarten_stats  — aggregated per-kindergarten statistics
* crawl_runs          — operational log of each crawl execution
* data_quality_issues — flagged data anomalies

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
_DEFAULT_DB_PATH = _HERE.parent / "data" / "kindergarten_reviews_master.db"

# Cache engines by resolved absolute path so each distinct SQLite file
# gets its own engine. This prevents test isolation issues where the first
# test's engine is reused for subsequent tests that use different temp paths.
_engines: dict[str, Engine] = {}

metadata = MetaData()


# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 1. kindergartens
# ---------------------------------------------------------------------------

kindergartens_table = Table(
    "kindergartens",
    metadata,

    Column(
        "id",
        Integer,
        primary_key=True,
        autoincrement=True,
    ),

    # MasterBuilder 使用：
    # ON CONFLICT (official_source_id) DO UPDATE
    #
    # 因此 official_source_id 必須是 UNIQUE。
    Column(
        "official_source_id",
        Text,
        unique=True,
    ),

    Column(
        "official_name",
        Text,
        nullable=False,
    ),

    Column(
        "school_name",
        Text,
    ),

    Column(
        "kindergarten_name",
        Text,
    ),

    Column(
        "kindergarten_type",
        Text,
    ),

    Column(
        "public_type",
        Text,
    ),

    Column(
        "district",
        Text,
        nullable=False,
    ),

    Column(
        "address",
        Text,
    ),

    Column(
        "postal_code",
        Text,
    ),

    Column(
        "phone",
        Text,
    ),

    Column(
        "latitude",
        REAL,
    ),

    Column(
        "longitude",
        REAL,
    ),

    Column(
        "official_source",
        Text,
    ),

    Column(
        "official_source_url",
        Text,
    ),

    Column(
        "is_public",
        Integer,
        default=1,
    ),

    Column(
        "status",
        Text,
        default="ACTIVE",
    ),

    Column(
        "created_at",
        Text,
    ),

    Column(
        "updated_at",
        Text,
    ),
)


Index(
    "ix_master_kindergartens_district",
    kindergartens_table.c.district,
)

Index(
    "ix_master_kindergartens_official_name",
    kindergartens_table.c.official_name,
)

Index(
    "ix_master_kindergartens_is_public",
    kindergartens_table.c.is_public,
)


# ---------------------------------------------------------------------------
# 2. places
# ---------------------------------------------------------------------------

places_table = Table(
    "places",
    metadata,

    Column(
        "id",
        Integer,
        primary_key=True,
        autoincrement=True,
    ),

    Column(
        "kindergarten_id",
        Integer,
        nullable=False,
    ),

    Column(
        "official_name",
        Text,
    ),

    Column(
        "district",
        Text,
    ),

    Column(
        "official_address",
        Text,
    ),

    Column(
        "google_place_id",
        Text,
        nullable=False,
    ),

    Column(
        "google_name",
        Text,
    ),

    Column(
        "google_address",
        Text,
    ),

    Column(
        "google_latitude",
        REAL,
    ),

    Column(
        "google_longitude",
        REAL,
    ),

    Column(
        "google_rating",
        REAL,
    ),

    Column(
        "google_user_rating_count",
        Integer,
    ),

    Column(
        "google_maps_uri",
        Text,
    ),

    Column(
        "business_status",
        Text,
    ),

    Column(
        "match_score",
        REAL,
    ),

    Column(
        "name_score",
        REAL,
    ),

    Column(
        "address_score",
        REAL,
    ),

    Column(
        "geo_score",
        REAL,
    ),

    Column(
        "match_status",
        Text,
        default="PENDING",
    ),

    Column(
        "match_method",
        Text,
    ),

    Column(
        "raw_json",
        Text,
    ),

    Column(
        "collection_scope",
        Text,
        default="GOOGLE_PLACES_API_PARTIAL",
    ),

    Column(
        "first_seen_at",
        Text,
    ),

    Column(
        "last_seen_at",
        Text,
    ),

    # MasterBuilder 使用：
    #
    # ON CONFLICT (kindergarten_id, google_place_id) DO UPDATE
    #
    # 因此這兩欄必須形成複合 UNIQUE constraint。
    UniqueConstraint(
        "kindergarten_id",
        "google_place_id",
        name="uq_master_places_kindergarten_google_place",
    ),
)


Index(
    "ix_master_places_kindergarten_id",
    places_table.c.kindergarten_id,
)

Index(
    "ix_master_places_district",
    places_table.c.district,
)

Index(
    "ix_master_places_google_place_id",
    places_table.c.google_place_id,
)

Index(
    "ix_master_places_match_status",
    places_table.c.match_status,
)


# ---------------------------------------------------------------------------
# 3. reviews
# ---------------------------------------------------------------------------

reviews_table = Table(
    "reviews",
    metadata,

    Column(
        "id",
        Integer,
        primary_key=True,
        autoincrement=True,
    ),

    Column(
        "kindergarten_id",
        Integer,
        nullable=False,
    ),

    Column(
        "district",
        Text,
        nullable=False,
    ),

    Column(
        "place_id",
        Text,
        nullable=False,
    ),

    Column(
        "review_resource_name",
        Text,
    ),

    Column(
        "author_display_name",
        Text,
    ),

    Column(
        "author_uri",
        Text,
    ),

    Column(
        "author_photo_uri",
        Text,
    ),

    Column(
        "rating",
        Integer,
        nullable=False,
    ),

    Column(
        "text",
        Text,
    ),

    Column(
        "original_text",
        Text,
    ),

    Column(
        "text_language",
        Text,
    ),

    Column(
        "original_language",
        Text,
    ),

    Column(
        "publish_time",
        Text,
    ),

    Column(
        "relative_publish_time",
        Text,
    ),

    Column(
        "review_uri",
        Text,
    ),

    Column(
        "source",
        Text,
        default="GOOGLE_PLACES_API",
    ),

    Column(
        "content_hash",
        Text,
        nullable=False,
    ),

    Column(
        "first_seen_at",
        Text,
    ),

    Column(
        "last_seen_at",
        Text,
    ),

    Column(
        "raw_json",
        Text,
    ),

    # 防止重複評論。
    UniqueConstraint(
        "content_hash",
        name="uq_master_reviews_content_hash",
    ),
)


Index(
    "ix_master_reviews_kindergarten_id",
    reviews_table.c.kindergarten_id,
)

Index(
    "ix_master_reviews_district",
    reviews_table.c.district,
)

Index(
    "ix_master_reviews_place_id",
    reviews_table.c.place_id,
)

Index(
    "ix_master_reviews_rating",
    reviews_table.c.rating,
)

Index(
    "ix_master_reviews_publish_time",
    reviews_table.c.publish_time,
)

Index(
    "ix_master_reviews_content_hash",
    reviews_table.c.content_hash,
)


# ---------------------------------------------------------------------------
# 4. district_stats
# ---------------------------------------------------------------------------

district_stats_table = Table(
    "district_stats",
    metadata,

    Column(
        "district",
        Text,
        primary_key=True,
    ),

    Column(
        "public_kindergarten_count",
        Integer,
        default=0,
    ),

    Column(
        "matched_place_count",
        Integer,
        default=0,
    ),

    Column(
        "unmatched_place_count",
        Integer,
        default=0,
    ),

    Column(
        "total_google_review_count",
        Integer,
        default=0,
    ),

    Column(
        "collected_review_count",
        Integer,
        default=0,
    ),

    Column(
        "avg_google_rating",
        REAL,
    ),

    Column(
        "avg_collected_rating",
        REAL,
    ),

    Column(
        "rating_1_count",
        Integer,
        default=0,
    ),

    Column(
        "rating_2_count",
        Integer,
        default=0,
    ),

    Column(
        "rating_3_count",
        Integer,
        default=0,
    ),

    Column(
        "rating_4_count",
        Integer,
        default=0,
    ),

    Column(
        "rating_5_count",
        Integer,
        default=0,
    ),

    Column(
        "last_sync_at",
        Text,
    ),
)


# ---------------------------------------------------------------------------
# 5. kindergarten_stats
# ---------------------------------------------------------------------------

kindergarten_stats_table = Table(
    "kindergarten_stats",
    metadata,

    Column(
        "kindergarten_id",
        Integer,
        primary_key=True,
    ),

    Column(
        "official_name",
        Text,
    ),

    Column(
        "district",
        Text,
    ),

    Column(
        "place_id",
        Text,
    ),

    Column(
        "google_rating",
        REAL,
    ),

    Column(
        "google_review_count",
        Integer,
        default=0,
    ),

    Column(
        "collected_review_count",
        Integer,
        default=0,
    ),

    Column(
        "rating_1_count",
        Integer,
        default=0,
    ),

    Column(
        "rating_2_count",
        Integer,
        default=0,
    ),

    Column(
        "rating_3_count",
        Integer,
        default=0,
    ),

    Column(
        "rating_4_count",
        Integer,
        default=0,
    ),

    Column(
        "rating_5_count",
        Integer,
        default=0,
    ),

    Column(
        "avg_collected_rating",
        REAL,
    ),

    Column(
        "latest_review_time",
        Text,
    ),

    Column(
        "oldest_review_time",
        Text,
    ),

    Column(
        "last_sync_at",
        Text,
    ),
)


Index(
    "ix_master_kstats_district",
    kindergarten_stats_table.c.district,
)


# ---------------------------------------------------------------------------
# 6. crawl_runs
# ---------------------------------------------------------------------------

crawl_runs_table = Table(
    "crawl_runs",
    metadata,

    Column(
        "id",
        Integer,
        primary_key=True,
        autoincrement=True,
    ),

    Column(
        "crawler",
        Text,
    ),

    Column(
        "district",
        Text,
    ),

    Column(
        "started_at",
        Text,
    ),

    Column(
        "finished_at",
        Text,
    ),

    Column(
        "total",
        Integer,
        default=0,
    ),

    Column(
        "success",
        Integer,
        default=0,
    ),

    Column(
        "failed",
        Integer,
        default=0,
    ),

    Column(
        "skipped",
        Integer,
        default=0,
    ),

    Column(
        "status",
        Text,
        default="PENDING",
    ),

    Column(
        "error",
        Text,
    ),
)


Index(
    "ix_master_crawl_runs_crawler",
    crawl_runs_table.c.crawler,
)

Index(
    "ix_master_crawl_runs_district",
    crawl_runs_table.c.district,
)

Index(
    "ix_master_crawl_runs_status",
    crawl_runs_table.c.status,
)


# ---------------------------------------------------------------------------
# 7. data_quality_issues
# ---------------------------------------------------------------------------

data_quality_issues_table = Table(
    "data_quality_issues",
    metadata,

    Column(
        "id",
        Integer,
        primary_key=True,
        autoincrement=True,
    ),

    Column(
        "issue_type",
        Text,
        nullable=False,
    ),

    Column(
        "severity",
        Text,
        nullable=False,
    ),

    Column(
        "kindergarten_id",
        Integer,
    ),

    Column(
        "district",
        Text,
    ),

    Column(
        "description",
        Text,
    ),

    Column(
        "raw_value",
        Text,
    ),

    Column(
        "created_at",
        Text,
    ),

    Column(
        "resolved_at",
        Text,
    ),

    Column(
        "resolved",
        Integer,
        default=0,
    ),
)


Index(
    "ix_master_dqi_issue_type",
    data_quality_issues_table.c.issue_type,
)

Index(
    "ix_master_dqi_severity",
    data_quality_issues_table.c.severity,
)

Index(
    "ix_master_dqi_resolved",
    data_quality_issues_table.c.resolved,
)

Index(
    "ix_master_dqi_kindergarten_id",
    data_quality_issues_table.c.kindergarten_id,
)


# ---------------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------------

def get_engine(
    db_path: str | Path | None = None,
) -> Engine:
    """
    Return and cache the SQLAlchemy engine for kindergarten_reviews_master.db.

    Engines are cached per resolved absolute path, so each distinct SQLite
    file gets its own engine instance.  This is critical for test isolation:
    each test that passes a unique temp path receives its own engine rather
    than reusing the first test's engine.
    """

    global _engines

    resolved = (
        Path(db_path).resolve()
        if db_path
        else Path(
            os.environ.get(
                "MASTER_DB_PATH",
                str(_DEFAULT_DB_PATH),
            )
        ).resolve()
    )

    cache_key = str(resolved)

    if cache_key in _engines:
        return _engines[cache_key]

    resolved.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    engine = create_engine(
        f"sqlite:///{resolved}",
        echo=False,
        connect_args={
            "check_same_thread": False,
        },
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(
        dbapi_conn,
        _connection_record,
    ):
        cursor = dbapi_conn.cursor()

        cursor.execute(
            "PRAGMA journal_mode=WAL"
        )

        cursor.execute(
            "PRAGMA foreign_keys=ON"
        )

        cursor.execute(
            "PRAGMA synchronous=NORMAL"
        )

        cursor.close()

    _engines[cache_key] = engine

    return engine


def reset_engine(
    db_path: str | Path | None = None,
) -> None:
    """
    Dispose and remove the cached SQLAlchemy engine.

    If ``db_path`` is given, only that engine is disposed.
    If ``db_path`` is ``None``, ALL cached engines are disposed and cleared.

    Useful for tests and switching database paths.
    """

    global _engines

    if db_path is not None:
        resolved_key = str(Path(db_path).resolve())
        engine = _engines.pop(resolved_key, None)
        if engine is not None:
            engine.dispose()
    else:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()


def create_tables(
    db_path: str | Path | None = None,
) -> None:
    """
    Create all master DB tables and indexes.
    """

    engine = get_engine(
        db_path
    )

    metadata.create_all(
        engine
    )


def get_connection(
    db_path: str | Path | None = None,
) -> Connection:
    """
    Return an open SQLAlchemy connection.
    """

    return get_engine(
        db_path
    ).connect()


# ---------------------------------------------------------------------------
# Crawl-run helpers
# ---------------------------------------------------------------------------

def start_crawl_run(
    crawler: str,
    district: str | None,
    started_at: str,
    db_path: str | Path | None = None,
) -> int:
    """
    Insert a crawl run with RUNNING status and return its ID.
    """

    with get_connection(
        db_path
    ) as conn:

        result = conn.execute(
            crawl_runs_table.insert().values(
                crawler=crawler,
                district=district,
                started_at=started_at,
                status="RUNNING",
                total=0,
                success=0,
                failed=0,
                skipped=0,
            )
        )

        conn.commit()

        return int(
            result.lastrowid
        )


def finish_crawl_run(
    run_id: int,
    finished_at: str,
    total: int = 0,
    success: int = 0,
    failed: int = 0,
    skipped: int = 0,
    status: str = "DONE",
    error: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """
    Update a crawl run when processing finishes.
    """

    with get_connection(
        db_path
    ) as conn:

        conn.execute(
            crawl_runs_table.update()
            .where(
                crawl_runs_table.c.id == run_id
            )
            .values(
                finished_at=finished_at,
                total=total,
                success=success,
                failed=failed,
                skipped=skipped,
                status=status,
                error=error,
            )
        )

        conn.commit()


# ---------------------------------------------------------------------------
# Data quality helpers
# ---------------------------------------------------------------------------

def log_quality_issue(
    issue_type: str,
    severity: str,
    description: str,
    kindergarten_id: int | None = None,
    district: str | None = None,
    raw_value: str | None = None,
    created_at: str | None = None,
    db_path: str | Path | None = None,
) -> int:
    """
    Insert a data quality issue.
    """

    from datetime import datetime, timezone

    timestamp = (
        created_at
        or datetime.now(
            timezone.utc
        ).isoformat()
    )

    with get_connection(
        db_path
    ) as conn:

        result = conn.execute(
            data_quality_issues_table
            .insert()
            .values(
                issue_type=issue_type,
                severity=severity,
                kindergarten_id=kindergarten_id,
                district=district,
                description=description,
                raw_value=raw_value,
                created_at=timestamp,
                resolved=0,
            )
        )

        conn.commit()

        return int(
            result.lastrowid
        )


def resolve_quality_issue(
    issue_id: int,
    resolved_at: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """
    Mark a data quality issue as resolved.
    """

    from datetime import datetime, timezone

    timestamp = (
        resolved_at
        or datetime.now(
            timezone.utc
        ).isoformat()
    )

    with get_connection(
        db_path
    ) as conn:

        conn.execute(
            data_quality_issues_table
            .update()
            .where(
                data_quality_issues_table.c.id
                == issue_id
            )
            .values(
                resolved=1,
                resolved_at=timestamp,
            )
        )

        conn.commit()


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def upsert_district_stats(
    record: dict,
    db_path: str | Path | None = None,
) -> None:
    """
    Insert or update district statistics.

    Conflict target:
        district
    """

    from sqlalchemy.dialects.sqlite import (
        insert as sqlite_insert,
    )

    allowed_cols = {
        column.key
        for column in district_stats_table.columns
    }

    clean = {
        key: value
        for key, value in record.items()
        if key in allowed_cols
    }

    if "district" not in clean:
        raise ValueError(
            "district is required"
        )

    update_cols = {
        key: value
        for key, value in clean.items()
        if key != "district"
    }

    with get_connection(
        db_path
    ) as conn:

        stmt = (
            sqlite_insert(
                district_stats_table
            )
            .values(
                **clean
            )
            .on_conflict_do_update(
                index_elements=[
                    "district"
                ],
                set_=update_cols,
            )
        )

        conn.execute(
            stmt
        )

        conn.commit()


def upsert_kindergarten_stats(
    record: dict,
    db_path: str | Path | None = None,
) -> None:
    """
    Insert or update kindergarten statistics.

    Conflict target:
        kindergarten_id
    """

    from sqlalchemy.dialects.sqlite import (
        insert as sqlite_insert,
    )

    allowed_cols = {
        column.key
        for column in kindergarten_stats_table.columns
    }

    clean = {
        key: value
        for key, value in record.items()
        if key in allowed_cols
    }

    if "kindergarten_id" not in clean:
        raise ValueError(
            "kindergarten_id is required"
        )

    update_cols = {
        key: value
        for key, value in clean.items()
        if key != "kindergarten_id"
    }

    with get_connection(
        db_path
    ) as conn:

        stmt = (
            sqlite_insert(
                kindergarten_stats_table
            )
            .values(
                **clean
            )
            .on_conflict_do_update(
                index_elements=[
                    "kindergarten_id"
                ],
                set_=update_cols,
            )
        )

        conn.execute(
            stmt
        )

        conn.commit()


# ---------------------------------------------------------------------------
# Stats readers
# ---------------------------------------------------------------------------

def get_district_stats(
    district: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict]:
    """
    Fetch district statistics.
    """

    with get_connection(
        db_path
    ) as conn:

        stmt = (
            district_stats_table
            .select()
            .order_by(
                district_stats_table.c.district
            )
        )

        if district:
            stmt = stmt.where(
                district_stats_table.c.district
                == district
            )

        rows = (
            conn.execute(
                stmt
            )
            .mappings()
            .all()
        )

        return [
            dict(row)
            for row in rows
        ]


def get_kindergarten_stats(
    kindergarten_id: int | None = None,
    district: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict]:
    """
    Fetch kindergarten statistics.
    """

    with get_connection(
        db_path
    ) as conn:

        stmt = (
            kindergarten_stats_table
            .select()
        )

        if kindergarten_id is not None:

            stmt = stmt.where(
                kindergarten_stats_table
                .c.kindergarten_id
                == kindergarten_id
            )

        elif district is not None:

            stmt = stmt.where(
                kindergarten_stats_table
                .c.district
                == district
            )

        rows = (
            conn.execute(
                stmt
            )
            .mappings()
            .all()
        )

        return [
            dict(row)
            for row in rows
        ]


# ---------------------------------------------------------------------------
# General query helpers
# ---------------------------------------------------------------------------

def get_kindergartens(
    district: str | None = None,
    public_only: bool = True,
    db_path: str | Path | None = None,
) -> list[dict]:
    """
    Fetch kindergarten records.
    """

    with get_connection(
        db_path
    ) as conn:

        stmt = (
            kindergartens_table
            .select()
            .order_by(
                kindergartens_table.c.district,
                kindergartens_table.c.official_name,
            )
        )

        if district:
            stmt = stmt.where(
                kindergartens_table.c.district
                == district
            )

        if public_only:
            stmt = stmt.where(
                kindergartens_table.c.is_public
                == 1
            )

        rows = (
            conn.execute(
                stmt
            )
            .mappings()
            .all()
        )

        return [
            dict(row)
            for row in rows
        ]


def get_places(
    kindergarten_id: int | None = None,
    district: str | None = None,
    match_status: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict]:
    """
    Fetch Google Place records.
    """

    with get_connection(
        db_path
    ) as conn:

        stmt = (
            places_table
            .select()
        )

        if kindergarten_id is not None:
            stmt = stmt.where(
                places_table.c.kindergarten_id
                == kindergarten_id
            )

        if district is not None:
            stmt = stmt.where(
                places_table.c.district
                == district
            )

        if match_status is not None:
            stmt = stmt.where(
                places_table.c.match_status
                == match_status
            )

        rows = (
            conn.execute(
                stmt
            )
            .mappings()
            .all()
        )

        return [
            dict(row)
            for row in rows
        ]


def get_reviews(
    kindergarten_id: int | None = None,
    district: str | None = None,
    place_id: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict]:
    """
    Fetch Google review records.
    """

    with get_connection(
        db_path
    ) as conn:

        stmt = (
            reviews_table
            .select()
        )

        if kindergarten_id is not None:
            stmt = stmt.where(
                reviews_table.c.kindergarten_id
                == kindergarten_id
            )

        if district is not None:
            stmt = stmt.where(
                reviews_table.c.district
                == district
            )

        if place_id is not None:
            stmt = stmt.where(
                reviews_table.c.place_id
                == place_id
            )

        stmt = stmt.order_by(
            reviews_table.c.publish_time.desc()
        )

        rows = (
            conn.execute(
                stmt
            )
            .mappings()
            .all()
        )

        return [
            dict(row)
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

def get_counts(
    db_path: str | Path | None = None,
) -> dict[str, int]:
    """
    Return row counts for all master database tables.
    """

    result: dict[str, int] = {}

    tables = {
        "kindergartens": kindergartens_table,
        "places": places_table,
        "reviews": reviews_table,
        "district_stats": district_stats_table,
        "kindergarten_stats": kindergarten_stats_table,
        "crawl_runs": crawl_runs_table,
        "data_quality_issues": data_quality_issues_table,
    }

    with get_connection(
        db_path
    ) as conn:

        for name, table in tables.items():

            count = conn.execute(
                select(
                    func.count()
                ).select_from(
                    table
                )
            ).scalar_one()

            result[name] = int(
                count
            )

    return result


# ---------------------------------------------------------------------------
# Module initialisation
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    create_tables()

    print(
        "kindergarten_reviews_master.db "
        "— tables created successfully."
    )