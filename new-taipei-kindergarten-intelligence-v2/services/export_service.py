"""
services/export_service.py
--------------------------
Exports data from ``kindergarten_reviews_master.db`` to structured flat files
(CSV, JSON, JSONL) suitable for downstream analysis or distribution.

Output layout
-------------
::

    exports/
    ├── all/
    │   ├── public_kindergartens.csv
    │   ├── google_places.csv
    │   ├── reviews.csv
    │   ├── reviews.jsonl
    │   └── district_stats.csv
    └── by_district/
        └── {district}/
            ├── kindergartens.csv
            ├── places.csv
            ├── reviews.csv
            └── stats.json

Even districts with zero records get a directory and a ``stats.json`` with
all numeric fields set to zero.

Usage::

    svc = ExportService()
    summary = svc.export_all()
    print(summary)
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.new_taipei_districts import NEW_TAIPEI_DISTRICTS
from config.settings import settings
from utils.logging import setup_logging, get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Column definitions for CSV files
# ---------------------------------------------------------------------------

_KINDERGARTEN_COLS = [
    "id",
    "official_source_id",
    "official_name",
    "school_name",
    "kindergarten_name",
    "kindergarten_type",
    "public_type",
    "district",
    "address",
    "postal_code",
    "phone",
    "latitude",
    "longitude",
    "is_public",
    "status",
    "official_source",
    "official_source_url",
    "created_at",
    "updated_at",
]

_PLACES_COLS = [
    "id",
    "kindergarten_id",
    "official_name",
    "district",
    "official_address",
    "google_place_id",
    "google_name",
    "google_address",
    "google_latitude",
    "google_longitude",
    "google_rating",
    "google_user_rating_count",
    "google_maps_uri",
    "business_status",
    "match_score",
    "name_score",
    "address_score",
    "geo_score",
    "match_status",
    "match_method",
    "collection_scope",
    "first_seen_at",
    "last_seen_at",
]

_REVIEWS_COLS = [
    "id",
    "kindergarten_id",
    "district",
    "place_id",
    "author_display_name",
    "rating",
    "text",
    "original_text",
    "text_language",
    "original_language",
    "publish_time",
    "relative_publish_time",
    "review_uri",
    "source",
    "content_hash",
    "first_seen_at",
    "last_seen_at",
]

_DISTRICT_STATS_COLS = [
    "district",
    "public_kindergarten_count",
    "matched_place_count",
    "unmatched_place_count",
    "total_google_review_count",
    "collected_review_count",
    "avg_google_rating",
    "avg_collected_rating",
    "rating_1_count",
    "rating_2_count",
    "rating_3_count",
    "rating_4_count",
    "rating_5_count",
    "last_sync_at",
]


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    """Write *rows* to *path* as a UTF-8-with-BOM CSV (Excel-compatible)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    """Write *rows* to *path* as newline-delimited JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, data: Any) -> None:
    """Write *data* to *path* as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# ExportService
# ---------------------------------------------------------------------------


class ExportService:
    """Exports master-DB data to flat files.

    Parameters
    ----------
    master_db_path:
        Path to ``kindergarten_reviews_master.db``.  Defaults to
        ``settings.MASTER_DB``.
    exports_dir:
        Root directory for output files.  Defaults to
        ``settings.EXPORTS_DIR``.
    """

    def __init__(
        self,
        master_db_path: Path | None = None,
        exports_dir: Path | None = None,
    ) -> None:
        self.master_db_path = Path(master_db_path or settings.MASTER_DB)
        self.exports_dir = Path(exports_dir or settings.EXPORTS_DIR)

        self._all_dir = self.exports_dir / "all"
        self._by_district_dir = self.exports_dir / "by_district"

    # ------------------------------------------------------------------
    # Internal DB helpers
    # ------------------------------------------------------------------

    def _get_connection(self):
        """Return a master_db connection bound to our configured path."""
        import database.master_db as master_db

        return master_db.get_connection(self.master_db_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export_all(self) -> dict[str, Any]:
        """Export everything and return a summary dict.

        Steps
        -----
        1. Export ``all/`` flat files.
        2. Export one sub-directory per district (all 29), even if empty.

        Returns
        -------
        dict
            Keys: ``public_kindergartens_csv``, ``google_places_csv``,
            ``reviews_csv``, ``reviews_jsonl``, ``district_stats_csv``,
            ``districts_exported``, ``export_time``.
        """
        setup_logging()
        logger.info("ExportService.export_all() — started")

        kg_path = self._export_public_kindergartens_csv()
        pl_path = self._export_google_places_csv()
        rv_path = self._export_reviews_csv()
        rl_path = self._export_reviews_jsonl()
        ds_path = self._export_district_stats_csv()

        for district in NEW_TAIPEI_DISTRICTS:
            try:
                self._export_by_district(district)
            except Exception as exc:
                logger.error(
                    "export_all: failed to export district '%s': %s",
                    district,
                    exc,
                    exc_info=True,
                )

        summary = {
            "public_kindergartens_csv": kg_path,
            "google_places_csv": pl_path,
            "reviews_csv": rv_path,
            "reviews_jsonl": rl_path,
            "district_stats_csv": ds_path,
            "districts_exported": len(NEW_TAIPEI_DISTRICTS),
            "export_time": datetime.now(timezone.utc).isoformat(),
        }
        logger.info("ExportService.export_all() — finished: %s", summary)
        return summary

    def export_district(self, district: str) -> None:
        """Export all data for a single district.

        Parameters
        ----------
        district:
            District name, e.g. ``"板橋區"``.  Must be one of the 29 valid
            New Taipei districts.
        """
        setup_logging()
        logger.info("ExportService.export_district('%s') — started", district)
        self._export_by_district(district)
        logger.info("ExportService.export_district('%s') — finished", district)

    # ------------------------------------------------------------------
    # Private — all/ exports
    # ------------------------------------------------------------------

    def _export_public_kindergartens_csv(self) -> str:
        """Export all kindergartens to ``exports/all/public_kindergartens.csv``.

        Returns
        -------
        str
            Absolute path to the written file.
        """
        path = self._all_dir / "public_kindergartens.csv"
        try:
            with self._get_connection() as conn:
                import database.master_db as master_db

                rows = (
                    conn.execute(
                        master_db.kindergartens_table.select().order_by(
                            master_db.kindergartens_table.c.district,
                            master_db.kindergartens_table.c.official_name,
                        )
                    )
                    .mappings()
                    .all()
                )
            _write_csv(path, [dict(r) for r in rows], _KINDERGARTEN_COLS)
            logger.info(
                "_export_public_kindergartens_csv: %d rows → %s", len(rows), path
            )
        except Exception as exc:
            logger.error(
                "_export_public_kindergartens_csv failed: %s", exc, exc_info=True
            )
        return str(path)

    def _export_google_places_csv(self) -> str:
        """Export all place-match records to ``exports/all/google_places.csv``.

        Returns
        -------
        str
            Absolute path to the written file.
        """
        path = self._all_dir / "google_places.csv"
        try:
            with self._get_connection() as conn:
                import database.master_db as master_db

                rows = (
                    conn.execute(
                        master_db.places_table.select().order_by(
                            master_db.places_table.c.district,
                            master_db.places_table.c.official_name,
                        )
                    )
                    .mappings()
                    .all()
                )
            _write_csv(path, [dict(r) for r in rows], _PLACES_COLS)
            logger.info(
                "_export_google_places_csv: %d rows → %s", len(rows), path
            )
        except Exception as exc:
            logger.error(
                "_export_google_places_csv failed: %s", exc, exc_info=True
            )
        return str(path)

    def _export_reviews_csv(self) -> str:
        """Export all reviews to ``exports/all/reviews.csv``.

        Returns
        -------
        str
            Absolute path to the written file.
        """
        path = self._all_dir / "reviews.csv"
        try:
            with self._get_connection() as conn:
                import database.master_db as master_db

                rows = (
                    conn.execute(
                        master_db.reviews_table.select().order_by(
                            master_db.reviews_table.c.district,
                            master_db.reviews_table.c.publish_time.desc(),
                        )
                    )
                    .mappings()
                    .all()
                )
            _write_csv(path, [dict(r) for r in rows], _REVIEWS_COLS)
            logger.info("_export_reviews_csv: %d rows → %s", len(rows), path)
        except Exception as exc:
            logger.error("_export_reviews_csv failed: %s", exc, exc_info=True)
        return str(path)

    def _export_reviews_jsonl(self) -> str:
        """Export all reviews to ``exports/all/reviews.jsonl``.

        Raw_json is excluded from the JSONL to keep file size reasonable.

        Returns
        -------
        str
            Absolute path to the written file.
        """
        path = self._all_dir / "reviews.jsonl"
        try:
            with self._get_connection() as conn:
                import database.master_db as master_db

                rows = (
                    conn.execute(
                        master_db.reviews_table.select().order_by(
                            master_db.reviews_table.c.district,
                            master_db.reviews_table.c.publish_time.desc(),
                        )
                    )
                    .mappings()
                    .all()
                )

            clean_rows = []
            for r in rows:
                d = dict(r)
                d.pop("raw_json", None)
                clean_rows.append(d)

            _write_jsonl(path, clean_rows)
            logger.info("_export_reviews_jsonl: %d rows → %s", len(rows), path)
        except Exception as exc:
            logger.error("_export_reviews_jsonl failed: %s", exc, exc_info=True)
        return str(path)

    def _export_district_stats_csv(self) -> str:
        """Export district-level stats to ``exports/all/district_stats.csv``.

        Returns
        -------
        str
            Absolute path to the written file.
        """
        path = self._all_dir / "district_stats.csv"
        try:
            import database.master_db as master_db

            rows = master_db.get_district_stats(db_path=self.master_db_path)
            _write_csv(path, rows, _DISTRICT_STATS_COLS)
            logger.info(
                "_export_district_stats_csv: %d rows → %s", len(rows), path
            )
        except Exception as exc:
            logger.error(
                "_export_district_stats_csv failed: %s", exc, exc_info=True
            )
        return str(path)

    # ------------------------------------------------------------------
    # Private — by_district/ exports
    # ------------------------------------------------------------------

    def _export_by_district(self, district: str) -> None:
        """Export data for *district* to ``exports/by_district/{district}/``.

        Files written
        -------------
        * ``kindergartens.csv``
        * ``places.csv``
        * ``reviews.csv``
        * ``stats.json``

        Even when *district* has zero records every file is created (CSV with
        only a header; stats.json with all numeric fields = 0).

        Parameters
        ----------
        district:
            District name, e.g. ``"板橋區"``.
        """
        import database.master_db as master_db
        from sqlalchemy import func, select

        dist_dir = self._by_district_dir / district
        dist_dir.mkdir(parents=True, exist_ok=True)

        # ---- kindergartens.csv ------------------------------------------
        try:
            with self._get_connection() as conn:
                kg_rows = (
                    conn.execute(
                        master_db.kindergartens_table.select()
                        .where(
                            master_db.kindergartens_table.c.district == district
                        )
                        .order_by(master_db.kindergartens_table.c.official_name)
                    )
                    .mappings()
                    .all()
                )
            _write_csv(
                dist_dir / "kindergartens.csv",
                [dict(r) for r in kg_rows],
                _KINDERGARTEN_COLS,
            )
        except Exception as exc:
            logger.error(
                "_export_by_district '%s' — kindergartens.csv error: %s",
                district,
                exc,
                exc_info=True,
            )
            kg_rows = []

        # ---- places.csv -------------------------------------------------
        try:
            with self._get_connection() as conn:
                pl_rows = (
                    conn.execute(
                        master_db.places_table.select()
                        .where(master_db.places_table.c.district == district)
                        .order_by(master_db.places_table.c.official_name)
                    )
                    .mappings()
                    .all()
                )
            _write_csv(
                dist_dir / "places.csv",
                [dict(r) for r in pl_rows],
                _PLACES_COLS,
            )
        except Exception as exc:
            logger.error(
                "_export_by_district '%s' — places.csv error: %s",
                district,
                exc,
                exc_info=True,
            )
            pl_rows = []

        # ---- reviews.csv ------------------------------------------------
        try:
            with self._get_connection() as conn:
                rv_rows = (
                    conn.execute(
                        master_db.reviews_table.select()
                        .where(master_db.reviews_table.c.district == district)
                        .order_by(
                            master_db.reviews_table.c.publish_time.desc()
                        )
                    )
                    .mappings()
                    .all()
                )
            _write_csv(
                dist_dir / "reviews.csv",
                [dict(r) for r in rv_rows],
                _REVIEWS_COLS,
            )
        except Exception as exc:
            logger.error(
                "_export_by_district '%s' — reviews.csv error: %s",
                district,
                exc,
                exc_info=True,
            )
            rv_rows = []

        # ---- stats.json -------------------------------------------------
        try:
            db_stats = master_db.get_district_stats(
                district=district, db_path=self.master_db_path
            )
            if db_stats:
                stats_data = db_stats[0]
            else:
                # Synthesise zero-record stats
                stats_data = {
                    "district": district,
                    "public_kindergarten_count": len(kg_rows),
                    "matched_place_count": sum(
                        1
                        for r in pl_rows
                        if dict(r).get("match_status") == "AUTO_ACCEPT"
                    ),
                    "unmatched_place_count": 0,
                    "total_google_review_count": 0,
                    "collected_review_count": len(rv_rows),
                    "avg_google_rating": None,
                    "avg_collected_rating": None,
                    "rating_1_count": 0,
                    "rating_2_count": 0,
                    "rating_3_count": 0,
                    "rating_4_count": 0,
                    "rating_5_count": 0,
                    "last_sync_at": datetime.now(timezone.utc).isoformat(),
                }
            _write_json(dist_dir / "stats.json", stats_data)
        except Exception as exc:
            logger.error(
                "_export_by_district '%s' — stats.json error: %s",
                district,
                exc,
                exc_info=True,
            )
            # Fallback: write minimal stats.json so directory always has the file
            _write_json(
                dist_dir / "stats.json",
                {
                    "district": district,
                    "public_kindergarten_count": 0,
                    "matched_place_count": 0,
                    "unmatched_place_count": 0,
                    "total_google_review_count": 0,
                    "collected_review_count": 0,
                    "avg_google_rating": None,
                    "avg_collected_rating": None,
                    "rating_1_count": 0,
                    "rating_2_count": 0,
                    "rating_3_count": 0,
                    "rating_4_count": 0,
                    "rating_5_count": 0,
                    "last_sync_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc),
                },
            )

        logger.info(
            "_export_by_district '%s': kg=%d, places=%d, reviews=%d",
            district,
            len(kg_rows),
            len(pl_rows),
            len(rv_rows),
        )
