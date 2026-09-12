"""
SQLAlchemy 2.x ORM models for New Taipei Public Kindergarten Review Pipeline.

Design principles:
- government data (kindergartens) is the authority for public classification
- google data (google_places, reviews) is purely for Place matching and review collection
- these two layers must never be mixed for public/private determination
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PublicType(str, enum.Enum):
    """Normalized public classification from official government source."""
    PUBLIC = "public"            # 公立、市立、區立、國立
    PUBLIC_ATTACHED = "public_attached"  # 公立附設幼兒園 (e.g. 國小附設)
    PRIVATE = "private"          # 私立
    QUASI_PUBLIC = "quasi_public"  # 準公共
    NONPROFIT = "nonprofit"      # 非營利
    PUBLIC_PRIVATE_PARTNERSHIP = "public_private_partnership"  # 公設民營
    UNKNOWN = "unknown"          # 無法從來源欄位判定


class KindergartenStatus(str, enum.Enum):
    """Processing pipeline status for a kindergarten record."""
    PENDING = "pending"
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    INACTIVE = "inactive"


class GoogleMatchStatus(str, enum.Enum):
    """Result of Google Place matching."""
    UNMATCHED = "unmatched"      # not yet attempted
    AUTO_ACCEPTED = "auto_accepted"   # score >= 0.85
    NEEDS_REVIEW = "needs_review"    # 0.70 <= score < 0.85
    REJECTED = "rejected"        # score < 0.70
    MANUAL_OVERRIDE = "manual_override"  # from config/place_overrides.csv
    NO_RESULT = "no_result"      # API returned nothing


class PlaceFetchStatus(str, enum.Enum):
    """Status of fetching Place metadata / reviews."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


# ---------------------------------------------------------------------------
# Kindergarten (Government Master Dataset)
# ---------------------------------------------------------------------------


class Kindergarten(Base):
    """
    Official government-sourced kindergarten record.

    is_public=True is the ONLY gate for entry into Google matching pipeline.
    public classification is determined SOLELY by government open data.
    Google Maps is never used to determine public status.
    """
    __tablename__ = "kindergartens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Official government fields ---
    official_name: Mapped[str] = mapped_column(String(200), nullable=False, comment="完整官方名稱")
    school_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="學校名稱（國小本體名稱）")
    kindergarten_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="幼兒園部分名稱")

    # --- Public classification (from government source only) ---
    public_type: Mapped[PublicType] = mapped_column(
        Enum(PublicType), nullable=False, default=PublicType.UNKNOWN,
        comment="標準化公私立類型，由政府資料欄位判定"
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="True=公立，僅由政府資料判定，Google資料不可用於此欄位"
    )

    # --- Location ---
    district: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="行政區")
    address: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, comment="官方地址")
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="官方座標緯度")
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="官方座標經度")

    # --- Data source ---
    official_source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="資料來源名稱")
    official_source_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="來源系統的原始 ID")

    # --- Pipeline status ---
    status: Mapped[KindergartenStatus] = mapped_column(
        Enum(KindergartenStatus), nullable=False, default=KindergartenStatus.PENDING
    )

    # --- Google Place matching results (populated by collector/place_matcher) ---
    google_place_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    google_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    google_address: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    google_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    google_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    google_match_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="0.0-1.0 matching score")
    google_match_status: Mapped[GoogleMatchStatus] = mapped_column(
        Enum(GoogleMatchStatus), nullable=False, default=GoogleMatchStatus.UNMATCHED
    )
    google_maps_uri: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # --- Google Place fetch status ---
    place_fetch_status: Mapped[PlaceFetchStatus] = mapped_column(
        Enum(PlaceFetchStatus), nullable=False, default=PlaceFetchStatus.PENDING
    )
    place_fetch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    place_fetch_last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Review fetch status ---
    review_fetch_status: Mapped[PlaceFetchStatus] = mapped_column(
        Enum(PlaceFetchStatus), nullable=False, default=PlaceFetchStatus.PENDING
    )
    review_fetch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_fetch_last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # --- Relationships ---
    google_place: Mapped[Optional["GooglePlace"]] = relationship(
        "GooglePlace", back_populates="kindergarten", uselist=False
    )
    reviews: Mapped[list["Review"]] = relationship("Review", back_populates="kindergarten")
    review_stats: Mapped[Optional["KindergartenReviewStats"]] = relationship(
        "KindergartenReviewStats", back_populates="kindergarten", uselist=False
    )

    __table_args__ = (
        Index("ix_kindergartens_district", "district"),
        Index("ix_kindergartens_is_public", "is_public"),
        Index("ix_kindergartens_status", "status"),
        Index("ix_kindergartens_public_type", "public_type"),
        Index("ix_kindergartens_match_status", "google_match_status"),
        UniqueConstraint("official_source", "official_source_id", name="uq_kindergartens_source_id"),
    )

    def __repr__(self) -> str:
        return f"<Kindergarten id={self.id} name={self.official_name!r} public={self.is_public}>"


# ---------------------------------------------------------------------------
# Google Place Metadata
# ---------------------------------------------------------------------------


class GooglePlace(Base):
    """
    Google Places API metadata for a matched kindergarten.

    Populated after Place matching. One kindergarten -> at most one GooglePlace.
    Stores raw_response for debugging without re-calling the API.
    """
    __tablename__ = "google_places"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kindergarten_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kindergartens.id"), nullable=False, unique=True
    )
    place_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)

    display_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    formatted_address: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="Google overall rating (1-5)")
    user_rating_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Total review count as shown on Google Maps"
    )
    google_maps_uri: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    business_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Store full API JSON response for debug / re-processing
    raw_response: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # --- Relationships ---
    kindergarten: Mapped["Kindergarten"] = relationship("Kindergarten", back_populates="google_place")
    reviews: Mapped[list["Review"]] = relationship(
        "Review",
        back_populates="google_place",
        foreign_keys="[Review.google_place_fk_id]",
    )

    def __repr__(self) -> str:
        return f"<GooglePlace id={self.id} place_id={self.place_id!r} name={self.display_name!r}>"


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


class Review(Base):
    """
    Google review collected via Places API (New) Place Details.

    IMPORTANT LIMITATION:
    Google Places API (New) returns only a limited subset of reviews per Place.
    collected_review_count in KindergartenReviewStats will always be <= google_review_count.
    This dataset must NOT be presented as the complete historical Google Maps review corpus.

    Deduplication:
    - primary key: google_review_resource_name (if available)
    - fallback: (place_id, content_hash) unique constraint
    """
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kindergarten_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kindergartens.id"), nullable=False
    )
    # FK to google_places.id (nullable — review may be stored before place record is linked)
    google_place_fk_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("google_places.id"), nullable=True
    )
    place_id: Mapped[str] = mapped_column(String(200), nullable=False)

    # --- Google review identifiers ---
    google_review_resource_name: Mapped[Optional[str]] = mapped_column(
        String(300), nullable=True, unique=True, index=True,
        comment="Places API resource name e.g. places/xxx/reviews/yyy"
    )

    # --- Author ---
    author_display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    author_uri: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    author_photo_uri: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)

    # --- Content ---
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="1-5")
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="Translated or display text")
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="Original language text")
    text_language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    original_language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # --- Time ---
    relative_publish_time: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    publish_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # --- Links ---
    google_maps_uri: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    flag_content_uri: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # --- Collection metadata ---
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="google_places_api",
        comment="Must be 'google_places_api'; never claim completeness"
    )
    content_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True,
        comment="SHA256 of place_id+author+rating+original_text+publish_time"
    )

    # --- Dedup tracking ---
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # --- Raw API response ---
    raw_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # --- Relationships ---
    kindergarten: Mapped["Kindergarten"] = relationship("Kindergarten", back_populates="reviews")
    google_place: Mapped[Optional["GooglePlace"]] = relationship(
        "GooglePlace",
        back_populates="reviews",
        foreign_keys=[google_place_fk_id],
    )

    __table_args__ = (
        Index("ix_reviews_kindergarten_id", "kindergarten_id"),
        Index("ix_reviews_place_id", "place_id"),
        Index("ix_reviews_rating", "rating"),
        Index("ix_reviews_publish_time", "publish_time"),
        # Fallback unique: place_id + content_hash (when resource_name is absent)
        UniqueConstraint("place_id", "content_hash", name="uq_reviews_place_content"),
    )

    def __repr__(self) -> str:
        return (
            f"<Review id={self.id} place_id={self.place_id!r} "
            f"rating={self.rating} author={self.author_display_name!r}>"
        )


# ---------------------------------------------------------------------------
# Kindergarten Review Stats (aggregated)
# ---------------------------------------------------------------------------


class KindergartenReviewStats(Base):
    """
    Aggregated review statistics per kindergarten.

    google_review_count = total as reported by Google Place API (may be >> collected)
    collected_review_count = actual rows in reviews table for this kindergarten

    These two values MUST NOT be conflated.
    """
    __tablename__ = "kindergarten_review_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kindergarten_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kindergartens.id"), nullable=False, unique=True
    )
    place_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # From Google Place metadata
    google_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    google_review_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Total reviews shown on Google Maps"
    )

    # From our collected reviews
    collected_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rating_1_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rating_2_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rating_3_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rating_4_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rating_5_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_collected_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    latest_review_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    oldest_review_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # --- Relationship ---
    kindergarten: Mapped["Kindergarten"] = relationship(
        "Kindergarten", back_populates="review_stats"
    )

    def __repr__(self) -> str:
        return (
            f"<KindergartenReviewStats kindergarten_id={self.kindergarten_id} "
            f"google_total={self.google_review_count} collected={self.collected_review_count}>"
        )


# ---------------------------------------------------------------------------
# Rejected Kindergarten log (validation rejects)
# ---------------------------------------------------------------------------


class RejectedKindergarten(Base):
    """
    Log of records that failed validation.
    Preserves audit trail of why each record was excluded from the pipeline.
    """
    __tablename__ = "rejected_kindergartens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="private|quasi_public|nonprofit|not_new_taipei|unknown_type|missing_address|duplicate"
    )
    original_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="原始來源欄位值")
    address: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    raw_record: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    rejected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    def __repr__(self) -> str:
        return f"<RejectedKindergarten name={self.name!r} reason={self.reason!r}>"
