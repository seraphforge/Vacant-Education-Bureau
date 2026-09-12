"""
Repository layer — all database read/write operations go through here.

Keeps SQL logic out of service and collector layers.
All methods accept an explicit Session for testability.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from database.models import (
    GoogleMatchStatus,
    GooglePlace,
    Kindergarten,
    KindergartenReviewStats,
    KindergartenStatus,
    PlaceFetchStatus,
    PublicType,
    RejectedKindergarten,
    Review,
)


# ---------------------------------------------------------------------------
# Kindergarten repository
# ---------------------------------------------------------------------------


class KindergartenRepository:
    """CRUD and query operations for the kindergartens table."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- Create / upsert ---

    def upsert(self, data: dict) -> tuple[Kindergarten, bool]:
        """
        Insert or update a kindergarten record identified by (official_source, official_source_id).

        Returns:
            (kindergarten, created) where created=True means a new row was inserted.
        """
        existing = self.get_by_source_id(
            data.get("official_source", ""), data.get("official_source_id", "")
        )
        if existing:
            for key, value in data.items():
                if key not in ("id", "created_at"):
                    setattr(existing, key, value)
            existing.updated_at = datetime.utcnow()
            self.session.flush()
            return existing, False
        else:
            kg = Kindergarten(**data)
            self.session.add(kg)
            self.session.flush()
            return kg, True

    def bulk_insert(self, records: list[dict]) -> int:
        """
        Insert multiple kindergarten records, skipping duplicates.

        Returns:
            Number of rows actually inserted.
        """
        inserted = 0
        for record in records:
            _, created = self.upsert(record)
            if created:
                inserted += 1
        return inserted

    # --- Read ---

    def get_by_id(self, kindergarten_id: int) -> Optional[Kindergarten]:
        return self.session.get(Kindergarten, kindergarten_id)

    def get_by_source_id(self, source: str, source_id: str) -> Optional[Kindergarten]:
        stmt = select(Kindergarten).where(
            Kindergarten.official_source == source,
            Kindergarten.official_source_id == source_id,
        )
        return self.session.scalars(stmt).first()

    def get_all_public(self) -> Sequence[Kindergarten]:
        """Return all is_public=True kindergartens."""
        stmt = select(Kindergarten).where(Kindergarten.is_public == True)  # noqa: E712
        return self.session.scalars(stmt).all()

    def get_pending_match(self, limit: Optional[int] = None) -> Sequence[Kindergarten]:
        """Return public kindergartens that have not yet been Google-matched."""
        stmt = (
            select(Kindergarten)
            .where(
                Kindergarten.is_public == True,  # noqa: E712
                Kindergarten.google_match_status == GoogleMatchStatus.UNMATCHED,
            )
            .order_by(Kindergarten.id)
        )
        if limit:
            stmt = stmt.limit(limit)
        return self.session.scalars(stmt).all()

    def get_pending_fetch(
        self,
        limit: Optional[int] = None,
        district: Optional[str] = None,
    ) -> Sequence[Kindergarten]:
        """Return matched kindergartens whose Place metadata has not been fetched."""
        stmt = (
            select(Kindergarten)
            .where(
                Kindergarten.is_public == True,  # noqa: E712
                Kindergarten.google_match_status == GoogleMatchStatus.AUTO_ACCEPTED,
                Kindergarten.place_fetch_status.in_(
                    [PlaceFetchStatus.PENDING, PlaceFetchStatus.FAILED]
                ),
            )
            .order_by(Kindergarten.id)
        )
        if district:
            stmt = stmt.where(Kindergarten.district == district)
        if limit:
            stmt = stmt.limit(limit)
        return self.session.scalars(stmt).all()

    def get_pending_review_fetch(
        self,
        limit: Optional[int] = None,
        district: Optional[str] = None,
    ) -> Sequence[Kindergarten]:
        """Return kindergartens whose reviews have not been collected yet."""
        stmt = (
            select(Kindergarten)
            .where(
                Kindergarten.is_public == True,  # noqa: E712
                Kindergarten.google_match_status == GoogleMatchStatus.AUTO_ACCEPTED,
                Kindergarten.place_fetch_status == PlaceFetchStatus.SUCCESS,
                Kindergarten.review_fetch_status.in_(
                    [PlaceFetchStatus.PENDING, PlaceFetchStatus.FAILED]
                ),
            )
            .order_by(Kindergarten.id)
        )
        if district:
            stmt = stmt.where(Kindergarten.district == district)
        if limit:
            stmt = stmt.limit(limit)
        return self.session.scalars(stmt).all()

    # --- Counts ---

    def count_total(self) -> int:
        return self.session.scalar(select(func.count()).select_from(Kindergarten)) or 0

    def count_public(self) -> int:
        return (
            self.session.scalar(
                select(func.count()).select_from(Kindergarten).where(Kindergarten.is_public == True)  # noqa: E712
            )
            or 0
        )

    def count_rejected(self) -> int:
        return (
            self.session.scalar(
                select(func.count())
                .select_from(Kindergarten)
                .where(Kindergarten.status == KindergartenStatus.REJECTED)
            )
            or 0
        )

    def count_by_match_status(self, status: GoogleMatchStatus) -> int:
        return (
            self.session.scalar(
                select(func.count())
                .select_from(Kindergarten)
                .where(
                    Kindergarten.is_public == True,  # noqa: E712
                    Kindergarten.google_match_status == status,
                )
            )
            or 0
        )

    def count_by_public_type(self, public_type: PublicType) -> int:
        return (
            self.session.scalar(
                select(func.count())
                .select_from(Kindergarten)
                .where(Kindergarten.public_type == public_type)
            )
            or 0
        )

    def district_breakdown(self) -> dict[str, int]:
        """Return {district: count} for public kindergartens."""
        rows = (
            self.session.execute(
                select(Kindergarten.district, func.count().label("cnt"))
                .where(Kindergarten.is_public == True)  # noqa: E712
                .group_by(Kindergarten.district)
                .order_by(Kindergarten.district)
            )
            .all()
        )
        return {row.district or "未知": row.cnt for row in rows}

    # --- Update ---

    def update_match_result(
        self,
        kindergarten_id: int,
        place_id: str,
        google_name: str,
        google_address: str,
        google_lat: Optional[float],
        google_lng: Optional[float],
        match_score: float,
        match_status: GoogleMatchStatus,
        google_maps_uri: Optional[str] = None,
    ) -> None:
        """Persist Google Place matching results onto a kindergarten row."""
        self.session.execute(
            update(Kindergarten)
            .where(Kindergarten.id == kindergarten_id)
            .values(
                google_place_id=place_id,
                google_name=google_name,
                google_address=google_address,
                google_latitude=google_lat,
                google_longitude=google_lng,
                google_match_score=match_score,
                google_match_status=match_status,
                google_maps_uri=google_maps_uri,
                updated_at=datetime.utcnow(),
            )
        )

    def update_place_fetch_status(
        self,
        kindergarten_id: int,
        status: PlaceFetchStatus,
        error: Optional[str] = None,
    ) -> None:
        self.session.execute(
            update(Kindergarten)
            .where(Kindergarten.id == kindergarten_id)
            .values(
                place_fetch_status=status,
                place_fetch_attempts=Kindergarten.place_fetch_attempts + 1,
                place_fetch_last_error=error,
                updated_at=datetime.utcnow(),
            )
        )

    def update_review_fetch_status(
        self,
        kindergarten_id: int,
        status: PlaceFetchStatus,
        error: Optional[str] = None,
    ) -> None:
        self.session.execute(
            update(Kindergarten)
            .where(Kindergarten.id == kindergarten_id)
            .values(
                review_fetch_status=status,
                review_fetch_attempts=Kindergarten.review_fetch_attempts + 1,
                review_fetch_last_error=error,
                updated_at=datetime.utcnow(),
            )
        )

    def apply_manual_override(
        self,
        kindergarten_id: int,
        place_id: str,
        note: Optional[str] = None,
    ) -> None:
        """Apply a manual place_overrides.csv entry."""
        self.session.execute(
            update(Kindergarten)
            .where(Kindergarten.id == kindergarten_id)
            .values(
                google_place_id=place_id,
                google_match_status=GoogleMatchStatus.MANUAL_OVERRIDE,
                updated_at=datetime.utcnow(),
            )
        )


# ---------------------------------------------------------------------------
# Google Place repository
# ---------------------------------------------------------------------------


class GooglePlaceRepository:
    """CRUD operations for google_places table."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, data: dict) -> tuple[GooglePlace, bool]:
        """Insert or update by place_id."""
        existing = self.get_by_place_id(data["place_id"])
        if existing:
            for key, value in data.items():
                if key != "id":
                    setattr(existing, key, value)
            return existing, False
        gp = GooglePlace(**data)
        self.session.add(gp)
        self.session.flush()
        return gp, True

    def get_by_place_id(self, place_id: str) -> Optional[GooglePlace]:
        stmt = select(GooglePlace).where(GooglePlace.place_id == place_id)
        return self.session.scalars(stmt).first()

    def get_by_kindergarten_id(self, kindergarten_id: int) -> Optional[GooglePlace]:
        stmt = select(GooglePlace).where(GooglePlace.kindergarten_id == kindergarten_id)
        return self.session.scalars(stmt).first()

    def count_fetched(self) -> int:
        return self.session.scalar(select(func.count()).select_from(GooglePlace)) or 0


# ---------------------------------------------------------------------------
# Review repository
# ---------------------------------------------------------------------------


class ReviewRepository:
    """CRUD and dedup operations for the reviews table."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, data: dict) -> tuple[Review, bool]:
        """
        Insert a review or update last_seen_at if it already exists.

        Dedup logic:
        1. If google_review_resource_name is present: match on that.
        2. Otherwise: match on (place_id, content_hash).

        Returns:
            (review, created) — created=True means a new row was inserted.
        """
        existing: Optional[Review] = None

        resource_name = data.get("google_review_resource_name")
        if resource_name:
            stmt = select(Review).where(Review.google_review_resource_name == resource_name)
            existing = self.session.scalars(stmt).first()

        if existing is None and data.get("place_id") and data.get("content_hash"):
            stmt = select(Review).where(
                Review.place_id == data["place_id"],
                Review.content_hash == data["content_hash"],
            )
            existing = self.session.scalars(stmt).first()

        if existing:
            existing.last_seen_at = datetime.utcnow()
            self.session.flush()
            return existing, False

        review = Review(**data)
        self.session.add(review)
        self.session.flush()
        return review, True

    def get_by_kindergarten(self, kindergarten_id: int) -> Sequence[Review]:
        stmt = select(Review).where(Review.kindergarten_id == kindergarten_id)
        return self.session.scalars(stmt).all()

    def count_total(self) -> int:
        return self.session.scalar(select(func.count()).select_from(Review)) or 0

    def count_by_rating(self, rating: int) -> int:
        return (
            self.session.scalar(
                select(func.count()).select_from(Review).where(Review.rating == rating)
            )
            or 0
        )

    def latest_sync_time(self) -> Optional[datetime]:
        return self.session.scalar(select(func.max(Review.last_seen_at)))


# ---------------------------------------------------------------------------
# Rejected Kindergarten repository
# ---------------------------------------------------------------------------


class RejectedKindergartenRepository:
    """Write-only repository for validation rejects."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, data: dict) -> RejectedKindergarten:
        rk = RejectedKindergarten(**data)
        self.session.add(rk)
        self.session.flush()
        return rk

    def count_by_reason(self, reason: str) -> int:
        return (
            self.session.scalar(
                select(func.count())
                .select_from(RejectedKindergarten)
                .where(RejectedKindergarten.reason == reason)
            )
            or 0
        )

    def count_total(self) -> int:
        return self.session.scalar(select(func.count()).select_from(RejectedKindergarten)) or 0

    def get_all(self) -> Sequence[RejectedKindergarten]:
        return self.session.scalars(select(RejectedKindergarten)).all()


# ---------------------------------------------------------------------------
# Stats repository
# ---------------------------------------------------------------------------


class StatsRepository:
    """Read/write for kindergarten_review_stats."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_stats(self, kindergarten_id: int, data: dict) -> KindergartenReviewStats:
        stmt = select(KindergartenReviewStats).where(
            KindergartenReviewStats.kindergarten_id == kindergarten_id
        )
        existing = self.session.scalars(stmt).first()
        if existing:
            for key, value in data.items():
                if key != "id":
                    setattr(existing, key, value)
            return existing
        stats = KindergartenReviewStats(kindergarten_id=kindergarten_id, **data)
        self.session.add(stats)
        self.session.flush()
        return stats

    def recompute_for_kindergarten(self, kindergarten_id: int, place_id: Optional[str] = None) -> None:
        """Recompute aggregated stats from the reviews table."""
        session = self.session
        counts = {}
        for star in range(1, 6):
            counts[star] = (
                session.scalar(
                    select(func.count())
                    .select_from(Review)
                    .where(Review.kindergarten_id == kindergarten_id, Review.rating == star)
                )
                or 0
            )
        total = sum(counts.values())
        avg = (
            sum(star * cnt for star, cnt in counts.items()) / total
            if total > 0
            else None
        )
        latest = session.scalar(
            select(func.max(Review.publish_time)).where(Review.kindergarten_id == kindergarten_id)
        )
        oldest = session.scalar(
            select(func.min(Review.publish_time)).where(Review.kindergarten_id == kindergarten_id)
        )

        self.upsert_stats(
            kindergarten_id,
            {
                "place_id": place_id,
                "collected_review_count": total,
                "rating_1_count": counts[1],
                "rating_2_count": counts[2],
                "rating_3_count": counts[3],
                "rating_4_count": counts[4],
                "rating_5_count": counts[5],
                "avg_collected_rating": avg,
                "latest_review_time": latest,
                "oldest_review_time": oldest,
                "last_sync_at": datetime.utcnow(),
            },
        )
