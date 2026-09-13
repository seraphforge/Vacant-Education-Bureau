"""
analysis/statistics.py
-----------------------
Builds ``district_stats`` and ``kindergarten_stats`` aggregations from the
three source databases (official_kindergartens, google_places, reviews) and
persists them to the master database.

Usage example::

    builder = StatisticsBuilder()
    district_stats  = builder.build_district_stats()
    kinder_stats    = builder.build_kindergarten_stats()
    builder.persist_to_master_db(district_stats, kinder_stats)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# StatisticsBuilder
# ---------------------------------------------------------------------------

# Match statuses that count as a successfully matched place
_ACCEPTED_MATCH_STATUSES = {"AUTO_ACCEPT", "MANUAL_REVIEW", "override"}

# All 29 valid New Taipei districts (guaranteed order for stable output)
_ALL_DISTRICTS: list[str] = [
    "板橋區", "三重區", "中和區", "永和區", "新莊區",
    "新店區", "樹林區", "鶯歌區", "三峽區", "淡水區",
    "汐止區", "瑞芳區", "土城區", "蘆洲區", "五股區",
    "泰山區", "林口區", "深坑區", "石碇區", "坪林區",
    "三芝區", "石門區", "八里區", "平溪區", "雙溪區",
    "貢寮區", "金山區", "萬里區", "烏來區",
]


class StatisticsBuilder:
    """Aggregate review and place statistics for all 29 New Taipei districts
    and for each individual kindergarten.

    Args:
        official_db_path: Path to ``official_kindergartens.db``.
        places_db_path:   Path to ``google_places.db``.
        reviews_db_path:  Path to ``reviews.db``.
        master_db_path:   Path to ``kindergarten_reviews_master.db``.
    """

    def __init__(
        self,
        official_db_path: Optional[str | Path] = None,
        places_db_path: Optional[str | Path] = None,
        reviews_db_path: Optional[str | Path] = None,
        master_db_path: Optional[str | Path] = None,
    ) -> None:
        self._official_db_path = official_db_path
        self._places_db_path = places_db_path
        self._reviews_db_path = reviews_db_path
        self._master_db_path = master_db_path

        # Data caches — populated lazily
        self._official_rows: list[dict] | None = None
        self._places_rows: list[dict] | None = None
        self._reviews_rows: list[dict] | None = None

    # ------------------------------------------------------------------
    # Internal data loaders
    # ------------------------------------------------------------------

    def _load_official(self) -> list[dict]:
        if self._official_rows is None:
            from database import official_db  # noqa: PLC0415

            self._official_rows = official_db.get_all_kindergartens(
                self._official_db_path
            )
        return self._official_rows

    def _load_places(self) -> list[dict]:
        if self._places_rows is None:
            from database import places_db  # noqa: PLC0415

            with places_db.get_connection(self._places_db_path) as conn:
                rows = conn.execute(
                    places_db.place_matches_table.select()
                ).mappings().all()
                self._places_rows = [dict(r) for r in rows]
        return self._places_rows

    def _load_reviews(self) -> list[dict]:
        if self._reviews_rows is None:
            from database import reviews_db  # noqa: PLC0415

            with reviews_db.get_connection(self._reviews_db_path) as conn:
                rows = conn.execute(
                    reviews_db.reviews_table.select()
                ).mappings().all()
                self._reviews_rows = [dict(r) for r in rows]
        return self._reviews_rows

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    @staticmethod
    def compute_avg_rating(ratings: list[int]) -> Optional[float]:
        """Compute the arithmetic mean of a list of integer ratings.

        Args:
            ratings: List of integer ratings (expected range 1–5).
                     Empty lists return ``None``.

        Returns:
            Average as a float rounded to 2 decimal places, or ``None``
            if the list is empty.
        """
        if not ratings:
            return None
        return round(sum(ratings) / len(ratings), 2)

    @staticmethod
    def get_rating_distribution(reviews: list[dict]) -> dict[int, int]:
        """Count how many reviews fall into each star rating (1–5).

        Args:
            reviews: List of review row dicts.  Each must have a ``"rating"``
                     key whose value is coercible to ``int``.

        Returns:
            A dict ``{1: n, 2: n, 3: n, 4: n, 5: n}`` where every star level
            is always present (defaulting to 0).
        """
        distribution: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for review in reviews:
            try:
                star = int(review.get("rating", 0))
                if 1 <= star <= 5:
                    distribution[star] += 1
            except (TypeError, ValueError):
                pass
        return distribution

    # ------------------------------------------------------------------
    # District statistics
    # ------------------------------------------------------------------

    def build_district_stats(self) -> list[dict]:
        """Build aggregated statistics for all 29 New Taipei districts.

        For each district the following fields are computed:

        * ``public_kindergarten_count``   — PUBLIC kindergartens in that district.
        * ``matched_place_count``         — kindergartens with an accepted place match.
        * ``unmatched_place_count``       — PUBLIC kindergartens without an accepted match.
        * ``total_google_review_count``   — sum of ``google_user_rating_count`` from places.
        * ``collected_review_count``      — reviews actually stored in reviews.db.
        * ``avg_google_rating``           — mean of ``google_rating`` across matched places.
        * ``avg_collected_rating``        — mean rating of collected reviews.
        * ``rating_1_count`` … ``rating_5_count`` — distribution of collected reviews.
        * ``last_sync_at``                — current UTC timestamp.

        Returns:
            One dict per district (29 total), ordered as ``_ALL_DISTRICTS``.
        """
        official_rows = self._load_official()
        places_rows = self._load_places()
        reviews_rows = self._load_reviews()

        now_ts = datetime.now(timezone.utc).isoformat()

        # Index public kindergartens by district
        public_by_district: dict[str, list[dict]] = {d: [] for d in _ALL_DISTRICTS}
        for row in official_rows:
            if not row.get("is_public"):
                continue
            district = (row.get("district") or "").strip()
            if district in public_by_district:
                public_by_district[district].append(row)

        # Index accepted place matches by (kindergarten_id) and district
        accepted_places_by_district: dict[str, list[dict]] = {
            d: [] for d in _ALL_DISTRICTS
        }
        accepted_kid_ids: set[int] = set()
        for row in places_rows:
            if row.get("match_status") in _ACCEPTED_MATCH_STATUSES:
                district = (row.get("district") or "").strip()
                if district in accepted_places_by_district:
                    accepted_places_by_district[district].append(row)
                kid_id = row.get("kindergarten_id")
                if kid_id is not None:
                    accepted_kid_ids.add(int(kid_id))

        # Index collected reviews by district
        reviews_by_district: dict[str, list[dict]] = {d: [] for d in _ALL_DISTRICTS}
        for row in reviews_rows:
            district = (row.get("district") or "").strip()
            if district in reviews_by_district:
                reviews_by_district[district].append(row)

        stats: list[dict] = []
        for district in _ALL_DISTRICTS:
            public_kids = public_by_district[district]
            accepted_places = accepted_places_by_district[district]
            district_reviews = reviews_by_district[district]

            matched_place_count = len(accepted_places)
            public_kindergarten_count = len(public_kids)
            unmatched_place_count = max(
                0, public_kindergarten_count - matched_place_count
            )

            # Total Google review count from place metadata
            total_google_review_count = sum(
                int(p.get("google_user_rating_count") or 0)
                for p in accepted_places
            )

            # Average Google rating
            google_ratings: list[float] = [
                float(p["google_rating"])
                for p in accepted_places
                if p.get("google_rating") is not None
            ]
            avg_google_rating = (
                round(sum(google_ratings) / len(google_ratings), 2)
                if google_ratings
                else None
            )

            # Collected review stats
            collected_review_count = len(district_reviews)
            collected_rating_list = [
                int(r["rating"])
                for r in district_reviews
                if r.get("rating") is not None
            ]
            avg_collected_rating = self.compute_avg_rating(collected_rating_list)
            dist = self.get_rating_distribution(district_reviews)

            stats.append(
                {
                    "district": district,
                    "public_kindergarten_count": public_kindergarten_count,
                    "matched_place_count": matched_place_count,
                    "unmatched_place_count": unmatched_place_count,
                    "total_google_review_count": total_google_review_count,
                    "collected_review_count": collected_review_count,
                    "avg_google_rating": avg_google_rating,
                    "avg_collected_rating": avg_collected_rating,
                    "rating_1_count": dist[1],
                    "rating_2_count": dist[2],
                    "rating_3_count": dist[3],
                    "rating_4_count": dist[4],
                    "rating_5_count": dist[5],
                    "last_sync_at": now_ts,
                }
            )

        logger.info(
            "Built district stats for %d districts.", len(stats)
        )
        return stats

    # ------------------------------------------------------------------
    # Kindergarten statistics
    # ------------------------------------------------------------------

    def build_kindergarten_stats(self) -> list[dict]:
        """Build per-kindergarten statistics for all PUBLIC kindergartens.

        For each PUBLIC kindergarten the following fields are computed:

        * ``kindergarten_id``           — PK from official_kindergartens.
        * ``official_name``
        * ``district``
        * ``place_id``                  — Google Place ID (if matched).
        * ``google_rating``             — Google rating from place match.
        * ``google_review_count``       — ``google_user_rating_count`` from place.
        * ``collected_review_count``    — reviews collected in reviews.db.
        * ``rating_1_count`` … ``rating_5_count``
        * ``avg_collected_rating``
        * ``latest_review_time``        — publish_time of most recent review.
        * ``oldest_review_time``        — publish_time of oldest review.
        * ``last_sync_at``              — current UTC timestamp.

        Returns:
            One dict per PUBLIC kindergarten, ordered by district then name.
        """
        official_rows = self._load_official()
        places_rows = self._load_places()
        reviews_rows = self._load_reviews()

        now_ts = datetime.now(timezone.utc).isoformat()

        # Index best accepted place match per kindergarten_id
        best_place_by_kid: dict[int, dict] = {}
        for row in places_rows:
            if row.get("match_status") not in _ACCEPTED_MATCH_STATUSES:
                continue
            kid_id = row.get("kindergarten_id")
            if kid_id is None:
                continue
            kid_id = int(kid_id)
            existing = best_place_by_kid.get(kid_id)
            if existing is None or (row.get("match_score") or 0) > (
                existing.get("match_score") or 0
            ):
                best_place_by_kid[kid_id] = row

        # Index reviews by kindergarten_id
        reviews_by_kid: dict[int, list[dict]] = {}
        for row in reviews_rows:
            kid_id = row.get("kindergarten_id")
            if kid_id is None:
                continue
            kid_id = int(kid_id)
            reviews_by_kid.setdefault(kid_id, []).append(row)

        stats: list[dict] = []
        # Process PUBLIC kindergartens only, sorted by district then name
        public_rows = sorted(
            (r for r in official_rows if r.get("is_public")),
            key=lambda r: (r.get("district") or "", r.get("official_name") or ""),
        )

        for row in public_rows:
            kid_id: int = int(row["id"])
            place = best_place_by_kid.get(kid_id)
            kid_reviews = reviews_by_kid.get(kid_id, [])

            # Google place data
            place_id: Optional[str] = place.get("google_place_id") if place else None
            google_rating: Optional[float] = (
                float(place["google_rating"]) if place and place.get("google_rating") is not None else None
            )
            google_review_count: int = (
                int(place.get("google_user_rating_count") or 0) if place else 0
            )

            # Collected review stats
            collected_review_count = len(kid_reviews)
            rating_list = [
                int(r["rating"]) for r in kid_reviews if r.get("rating") is not None
            ]
            avg_collected_rating = self.compute_avg_rating(rating_list)
            dist = self.get_rating_distribution(kid_reviews)

            # Review time bounds
            publish_times = [
                r["publish_time"]
                for r in kid_reviews
                if r.get("publish_time")
            ]
            publish_times_sorted = sorted(publish_times)
            latest_review_time = publish_times_sorted[-1] if publish_times_sorted else None
            oldest_review_time = publish_times_sorted[0] if publish_times_sorted else None

            stats.append(
                {
                    "kindergarten_id": kid_id,
                    "official_name": row.get("official_name"),
                    "district": row.get("district"),
                    "place_id": place_id,
                    "google_rating": google_rating,
                    "google_review_count": google_review_count,
                    "collected_review_count": collected_review_count,
                    "rating_1_count": dist[1],
                    "rating_2_count": dist[2],
                    "rating_3_count": dist[3],
                    "rating_4_count": dist[4],
                    "rating_5_count": dist[5],
                    "avg_collected_rating": avg_collected_rating,
                    "latest_review_time": latest_review_time,
                    "oldest_review_time": oldest_review_time,
                    "last_sync_at": now_ts,
                }
            )

        logger.info(
            "Built kindergarten stats for %d PUBLIC kindergartens.", len(stats)
        )
        return stats

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def persist_to_master_db(
        self,
        district_stats: list[dict],
        kindergarten_stats: list[dict],
        db_path: Optional[str | Path] = None,
    ) -> None:
        """Upsert all stats rows into the master database.

        Args:
            district_stats:     Output of :meth:`build_district_stats`.
            kindergarten_stats: Output of :meth:`build_kindergarten_stats`.
            db_path:            Override for master DB path.
        """
        from database import master_db  # noqa: PLC0415

        effective_path = db_path or self._master_db_path

        for record in district_stats:
            master_db.upsert_district_stats(record, effective_path)
        logger.info("Persisted %d district_stats rows.", len(district_stats))

        for record in kindergarten_stats:
            master_db.upsert_kindergarten_stats(record, effective_path)
        logger.info(
            "Persisted %d kindergarten_stats rows.", len(kindergarten_stats)
        )

    def build_and_persist(
        self, db_path: Optional[str | Path] = None
    ) -> tuple[list[dict], list[dict]]:
        """Convenience method: build both stat tables and persist them.

        Args:
            db_path: Override for master DB path.

        Returns:
            A ``(district_stats, kindergarten_stats)`` tuple.
        """
        district_stats = self.build_district_stats()
        kindergarten_stats = self.build_kindergarten_stats()
        self.persist_to_master_db(district_stats, kindergarten_stats, db_path)
        return district_stats, kindergarten_stats
