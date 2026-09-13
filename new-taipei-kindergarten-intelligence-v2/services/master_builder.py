"""
services/master_builder.py
--------------------------
Builds (or refreshes) ``kindergarten_reviews_master.db`` by integrating data
from the three source databases:

* ``data/db/official_kindergartens.db``  →  kindergartens table
* ``data/db/google_places.db``           →  places table
* ``data/db/reviews.db``                 →  reviews table

After syncing raw rows it computes aggregated statistics via
:class:`analysis.statistics.StatisticsBuilder`.

No external API calls are made — all data is read from local SQLite files.

Usage::

    builder = MasterBuilder()
    summary = builder.build()
    print(summary)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import settings
from utils.logging import setup_logging, get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW_UTC = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731


class MasterBuilder:
    """Integrates the three source databases into a single master database.

    Parameters
    ----------
    official_db_path:
        Path to ``official_kindergartens.db``.  Defaults to
        ``settings.OFFICIAL_DB``.
    places_db_path:
        Path to ``google_places.db``.  Defaults to ``settings.PLACES_DB``.
    reviews_db_path:
        Path to ``reviews.db``.  Defaults to ``settings.REVIEWS_DB``.
    master_db_path:
        Path to ``kindergarten_reviews_master.db``.  Defaults to
        ``settings.MASTER_DB``.
    """

    def __init__(
        self,
        official_db_path: Path | None = None,
        places_db_path: Path | None = None,
        reviews_db_path: Path | None = None,
        master_db_path: Path | None = None,
    ) -> None:
        self.official_db_path = Path(official_db_path or settings.OFFICIAL_DB)
        self.places_db_path = Path(places_db_path or settings.PLACES_DB)
        self.reviews_db_path = Path(reviews_db_path or settings.REVIEWS_DB)
        self.master_db_path = Path(master_db_path or settings.MASTER_DB)

        # Ensure parent directories exist
        self.master_db_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> dict[str, Any]:
        """Build the master database from the three source databases.

        Steps
        -----
        1. Ensure all tables exist in master_db.
        2. Sync kindergartens from official_db.
        3. Validate foreign keys in places table, then sync.
        4. Validate foreign keys in reviews table, then sync.
        5. Compute district_stats via StatisticsBuilder.
        6. Compute kindergarten_stats via StatisticsBuilder.

        Returns
        -------
        dict
            Summary statistics with keys:
            ``kindergartens``, ``places``, ``reviews``,
            ``fk_errors``, ``build_time``.
        """
        setup_logging()
        started_at = _NOW_UTC()
        logger.info("MasterBuilder.build() — started at %s", started_at)

        # 1. Ensure master DB tables exist
        self._init_master_db()

        # 2. Sync source tables
        kg_count = self._sync_kindergartens()
        place_count = self._sync_places()
        review_count = self._sync_reviews()

        # 3. Build aggregated statistics
        self._build_stats()

        # 4. Validate FKs and collect any residual errors for the summary
        fk_errors = self._validate_foreign_keys()
        if fk_errors:
            logger.warning(
                "FK validation found %d error(s) after sync — these records "
                "were already rejected and are NOT in master_db.",
                len(fk_errors),
            )

        finished_at = _NOW_UTC()
        summary = {
            "kindergartens": kg_count,
            "places": place_count,
            "reviews": review_count,
            "fk_errors": len(fk_errors),
            "build_time": finished_at,
            "started_at": started_at,
        }
        logger.info("MasterBuilder.build() — finished: %s", summary)
        return summary

    # ------------------------------------------------------------------
    # Private — initialisation
    # ------------------------------------------------------------------

    def _init_master_db(self) -> None:
        """Create tables in master_db if they do not already exist."""
        import database.master_db as master_db

        master_db.create_tables(self.master_db_path)
        logger.debug("Master DB tables ensured at %s", self.master_db_path)

    # ------------------------------------------------------------------
    # Private — sync methods
    # ------------------------------------------------------------------

    def _sync_kindergartens(self) -> int:
        """Copy PUBLIC kindergartens from official_db into master_db.

        Existing rows (matched on ``official_source_id``) are updated;
        new rows are inserted.  Rows with no district or district == UNKNOWN
        are logged as warnings but still imported.

        Returns
        -------
        int
            Number of rows upserted into master_db.kindergartens.
        """
        import database.official_db as official_db
        import database.master_db as master_db
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        if not self.official_db_path.exists():
            logger.warning(
                "official_db not found at %s — skipping kindergarten sync",
                self.official_db_path,
            )
            return 0

        rows = official_db.get_all_kindergartens(self.official_db_path)
        logger.info(
            "Syncing %d kindergarten(s) from official_db → master_db", len(rows)
        )

        upserted = 0
        now = _NOW_UTC()

        with master_db.get_connection(self.master_db_path) as conn:
            for row in rows:
                record = dict(row)
                # Ensure updated_at is current
                record.setdefault("updated_at", now)

                # Strip any keys not in the master schema
                allowed = {
                    c.key for c in master_db.kindergartens_table.columns
                }
                clean = {k: v for k, v in record.items() if k in allowed}

                stmt = sqlite_insert(master_db.kindergartens_table).values(**clean)
                if clean.get("official_source_id"):
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["official_source_id"],
                        set_={
                            k: v
                            for k, v in clean.items()
                            if k != "official_source_id"
                        },
                    )
                conn.execute(stmt)
                upserted += 1

            conn.commit()

        logger.info("_sync_kindergartens: %d rows upserted", upserted)
        return upserted

    def _sync_places(self) -> int:
        """Copy place-match records from places_db into master_db.

        FK validation is performed first: any record whose
        ``kindergarten_id`` does not exist in master_db.kindergartens is
        logged and rejected.

        Returns
        -------
        int
            Number of rows upserted into master_db.places.
        """
        import database.places_db as places_db
        import database.master_db as master_db
        from sqlalchemy import select
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        if not self.places_db_path.exists():
            logger.warning(
                "places_db not found at %s — skipping places sync",
                self.places_db_path,
            )
            return 0

        # Fetch all place_matches from source
        with places_db.get_connection(self.places_db_path) as conn:
            rows = (
                conn.execute(places_db.place_matches_table.select())
                .mappings()
                .all()
            )

        logger.info(
            "Syncing %d place record(s) from places_db → master_db", len(rows)
        )

        # Build set of valid kindergarten_ids in master_db
        with master_db.get_connection(self.master_db_path) as conn:
            valid_ids: set[int] = {
                row[0]
                for row in conn.execute(
                    select(master_db.kindergartens_table.c.id)
                ).fetchall()
            }

        rejected = 0
        upserted = 0
        allowed = {c.key for c in master_db.places_table.columns}

        with master_db.get_connection(self.master_db_path) as conn:
            for row in rows:
                record = dict(row)
                kg_id = record.get("kindergarten_id")

                # FK validation
                if kg_id not in valid_ids:
                    logger.warning(
                        "REJECTED place_match id=%s: kindergarten_id=%s not in "
                        "master_db.kindergartens",
                        record.get("id"),
                        kg_id,
                    )
                    rejected += 1
                    continue

                clean = {k: v for k, v in record.items() if k in allowed}
                # Remove source PK so master assigns its own
                clean.pop("id", None)

                stmt = sqlite_insert(master_db.places_table).values(**clean)
                if clean.get("kindergarten_id") and clean.get("google_place_id"):
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["kindergarten_id", "google_place_id"],
                        set_={
                            k: v
                            for k, v in clean.items()
                            if k not in ("kindergarten_id", "google_place_id")
                        },
                    )
                conn.execute(stmt)
                upserted += 1

            conn.commit()

        logger.info(
            "_sync_places: %d rows upserted, %d rejected (FK violations)",
            upserted,
            rejected,
        )
        return upserted

    def _sync_reviews(self) -> int:
        """Copy reviews from reviews_db into master_db.

        FK validation is performed first: any record whose
        ``kindergarten_id`` does not exist in master_db.kindergartens is
        logged and rejected.

        Returns
        -------
        int
            Number of rows upserted into master_db.reviews.
        """
        import database.reviews_db as reviews_db
        import database.master_db as master_db
        from sqlalchemy import select
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        if not self.reviews_db_path.exists():
            logger.warning(
                "reviews_db not found at %s — skipping reviews sync",
                self.reviews_db_path,
            )
            return 0

        # Fetch all reviews from source
        with reviews_db.get_connection(self.reviews_db_path) as conn:
            rows = (
                conn.execute(reviews_db.reviews_table.select())
                .mappings()
                .all()
            )

        logger.info(
            "Syncing %d review(s) from reviews_db → master_db", len(rows)
        )

        # Build set of valid kindergarten_ids in master_db
        with master_db.get_connection(self.master_db_path) as conn:
            valid_ids: set[int] = {
                row[0]
                for row in conn.execute(
                    select(master_db.kindergartens_table.c.id)
                ).fetchall()
            }

        rejected = 0
        upserted = 0
        allowed = {c.key for c in master_db.reviews_table.columns}

        with master_db.get_connection(self.master_db_path) as conn:
            for row in rows:
                record = dict(row)
                kg_id = record.get("kindergarten_id")

                # FK validation
                if kg_id not in valid_ids:
                    logger.warning(
                        "REJECTED review id=%s content_hash=%s: "
                        "kindergarten_id=%s not in master_db.kindergartens",
                        record.get("id"),
                        record.get("content_hash"),
                        kg_id,
                    )
                    rejected += 1
                    continue

                clean = {k: v for k, v in record.items() if k in allowed}
                # Remove source PK so master assigns its own
                clean.pop("id", None)

                stmt = sqlite_insert(master_db.reviews_table).values(**clean)
                if clean.get("content_hash"):
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["content_hash"],
                        set_={
                            k: v
                            for k, v in clean.items()
                            if k != "content_hash"
                        },
                    )
                conn.execute(stmt)
                upserted += 1

            conn.commit()

        logger.info(
            "_sync_reviews: %d rows upserted, %d rejected (FK violations)",
            upserted,
            rejected,
        )
        return upserted

    # ------------------------------------------------------------------
    # Private — statistics
    # ------------------------------------------------------------------

    def _build_stats(self) -> None:
        """Compute and persist both district-level and kindergarten-level
        aggregate statistics in a single call.

        Delegates to :class:`analysis.statistics.StatisticsBuilder`.
        Both stat sets are built first, then persisted together via a single
        call to ``persist_to_master_db(district_stats, kindergarten_stats)``.
        """
        try:
            from analysis.statistics import StatisticsBuilder

            builder = StatisticsBuilder(master_db_path=self.master_db_path)
            district_stats = builder.build_district_stats()
            kindergarten_stats = builder.build_kindergarten_stats()
            builder.persist_to_master_db(
                district_stats=district_stats,
                kindergarten_stats=kindergarten_stats,
            )
            logger.info(
                "_build_stats: persisted %d district stat(s) and %d "
                "kindergarten stat(s)",
                len(district_stats),
                len(kindergarten_stats),
            )
        except Exception as exc:
            logger.error("_build_stats failed: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Private — FK validation (post-sync audit)
    # ------------------------------------------------------------------

    def _validate_foreign_keys(self) -> list[str]:
        """Check referential integrity of places and reviews tables in master_db.

        Checks
        ------
        * Every ``places.kindergarten_id`` must exist in ``kindergartens.id``.
        * Every ``reviews.kindergarten_id`` must exist in ``kindergartens.id``.

        Any violations are logged as errors.  Because :meth:`_sync_places` and
        :meth:`_sync_reviews` already reject FK-invalid records during import,
        violations found here indicate a consistency issue that requires
        investigation.

        Returns
        -------
        list[str]
            Human-readable error strings (empty list if no violations).
        """
        import database.master_db as master_db
        from sqlalchemy import select, text

        errors: list[str] = []

        if not self.master_db_path.exists():
            logger.warning(
                "master_db not found at %s — cannot validate FK integrity",
                self.master_db_path,
            )
            return errors

        try:
            with master_db.get_connection(self.master_db_path) as conn:
                # --- places FK check -----------------------------------------
                bad_places = conn.execute(
                    text(
                        """
                        SELECT p.id, p.kindergarten_id
                        FROM places p
                        LEFT JOIN kindergartens k ON k.id = p.kindergarten_id
                        WHERE k.id IS NULL
                        """
                    )
                ).fetchall()

                for row in bad_places:
                    msg = (
                        f"FK VIOLATION places.id={row[0]} has "
                        f"kindergarten_id={row[1]} which does not exist in "
                        f"kindergartens"
                    )
                    logger.error(msg)
                    errors.append(msg)

                # --- reviews FK check ----------------------------------------
                bad_reviews = conn.execute(
                    text(
                        """
                        SELECT r.id, r.kindergarten_id
                        FROM reviews r
                        LEFT JOIN kindergartens k ON k.id = r.kindergarten_id
                        WHERE k.id IS NULL
                        """
                    )
                ).fetchall()

                for row in bad_reviews:
                    msg = (
                        f"FK VIOLATION reviews.id={row[0]} has "
                        f"kindergarten_id={row[1]} which does not exist in "
                        f"kindergartens"
                    )
                    logger.error(msg)
                    errors.append(msg)

        except Exception as exc:
            logger.error("FK validation query failed: %s", exc, exc_info=True)
            errors.append(f"FK validation query error: {exc}")

        if errors:
            logger.warning(
                "_validate_foreign_keys: %d FK violation(s) found", len(errors)
            )
        else:
            logger.info("_validate_foreign_keys: no FK violations found ✓")

        return errors
