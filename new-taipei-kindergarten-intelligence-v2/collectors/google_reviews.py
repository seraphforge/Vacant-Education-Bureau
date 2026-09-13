"""
collectors/google_reviews.py
------------------------------
Fetches Google Places reviews for every kindergarten that has been matched
(AUTO_ACCEPT status) in the place_matches table.

Endpoint
--------
GET https://places.googleapis.com/v1/places/{place_id}
    ?fields=reviews
    &languageCode=zh-TW
    &key={API_KEY}

The Google Places API (New) returns at most 5 reviews per place.
collection_scope is therefore recorded as GOOGLE_PLACES_API_PARTIAL.

Review deduplication
--------------------
Each review is identified by its content_hash (SHA-256 of place_id +
author_display_name + rating + original_text + publish_time).
Duplicates are silently skipped; last_seen_at is updated.

Resume support
--------------
Places whose reviews have already been collected in the current run
(presence of any review in reviews.db with matching place_id AND
last_seen_at >= run start) are skipped.  Pass force=True to re-collect.

Graceful degradation
--------------------
If the API key is missing or any API call fails, the collector logs a
warning and continues rather than raising an exception.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from collectors.base import BaseCollector, CrawlResult

logger = logging.getLogger(__name__)
matching_logger = logging.getLogger("matching")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLACES_DETAIL_URL = "https://places.googleapis.com/v1/places/{place_id}"

_MAX_RETRIES = 5
_BACKOFF_BASE = 2.0   # seconds

COLLECTION_SCOPE = "GOOGLE_PLACES_API_PARTIAL"

# Statuses that are eligible for review collection
_ELIGIBLE_STATUSES = {"AUTO_ACCEPT", "MANUAL_REVIEW"}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _fetch_reviews_for_place(
    place_id: str,
    api_key: str,
    client: httpx.Client,
    timeout: int = 30,
) -> list[dict]:
    """
    Fetch the reviews field for a single place.

    Returns a (possibly empty) list of review dicts.
    Raises RateLimitError / APIError / httpx exceptions on failure.
    """
    from utils.retry import handle_http_error

    url = PLACES_DETAIL_URL.format(place_id=place_id)
    params = {
        "fields": "reviews",
        "languageCode": "zh-TW",
        "key": api_key,
    }

    response = client.get(url, params=params, timeout=timeout)
    handle_http_error(response)

    data = response.json()
    return data.get("reviews", [])


def _fetch_with_retry(
    place_id: str,
    api_key: str,
    client: httpx.Client,
    timeout: int = 30,
    max_retries: int = _MAX_RETRIES,
) -> list[dict]:
    """
    Wrap _fetch_reviews_for_place with exponential back-off on transient
    errors (429, 5xx, network failures).
    """
    from utils.retry import RateLimitError, APIError

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            return _fetch_reviews_for_place(place_id, api_key, client, timeout=timeout)
        except RateLimitError as exc:
            last_exc = exc
            wait = _BACKOFF_BASE ** attempt
            logger.warning(
                "Rate limited fetching reviews for place_id=%s (attempt %d/%d). "
                "Waiting %.1fs.",
                place_id, attempt, max_retries, wait,
            )
            time.sleep(wait)
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "Transient error for place_id=%s attempt %d/%d (%s). "
                    "Retrying in %.1fs.",
                    place_id, attempt, max_retries, exc, wait,
                )
                time.sleep(wait)
            else:
                logger.error(
                    "All %d attempts failed for place_id=%s.", max_retries, place_id
                )
        except Exception as exc:
            # Non-retryable (e.g. APIError 400/404) — propagate immediately
            raise

    raise last_exc or RuntimeError(
        f"All retries exhausted for place_id={place_id!r}"
    )


# ---------------------------------------------------------------------------
# Review parsing helpers
# ---------------------------------------------------------------------------


def _parse_review(raw: dict) -> dict[str, Any]:
    """
    Parse a single raw review dict from the API into a normalised record.

    Returns a dict with all fields needed for reviews_table.
    """
    # Author info
    author = raw.get("authorAttribution", {})
    author_display_name: str = author.get("displayName", "")
    author_uri: str = author.get("uri", "")
    author_photo_uri: str = author.get("photoUri", "")

    # Rating
    raw_rating = raw.get("rating")
    try:
        rating = int(raw_rating)
    except (TypeError, ValueError):
        rating = 0  # will be filtered out later

    # Text content
    text_obj = raw.get("text", {})
    review_text: str = text_obj.get("text", "") if isinstance(text_obj, dict) else ""
    text_language: str = text_obj.get("languageCode", "") if isinstance(text_obj, dict) else ""

    orig_text_obj = raw.get("originalText", {})
    original_text: str = (
        orig_text_obj.get("text", "") if isinstance(orig_text_obj, dict) else ""
    )
    original_language: str = (
        orig_text_obj.get("languageCode", "") if isinstance(orig_text_obj, dict) else ""
    )

    # Timestamps
    publish_time: str = raw.get("publishTime", "") or raw.get("publishedTime", "")
    relative_publish_time: str = raw.get("relativePublishTimeDescription", "")

    # Resource name (e.g. "places/{place_id}/reviews/{review_id}")
    review_resource_name: str = raw.get("name", "")

    # Google Maps link for the review
    review_uri: str = raw.get("googleMapsUri", "")

    return {
        "review_resource_name": review_resource_name,
        "author_display_name": author_display_name,
        "author_uri": author_uri,
        "author_photo_uri": author_photo_uri,
        "rating": rating,
        "text": review_text,
        "original_text": original_text,
        "text_language": text_language,
        "original_language": original_language,
        "publish_time": publish_time,
        "relative_publish_time": relative_publish_time,
        "review_uri": review_uri,
    }


def _compute_content_hash(
    place_id: str,
    author_display_name: str,
    rating: int,
    original_text: str,
    publish_time: str,
) -> str:
    """Compute SHA-256 content hash for review deduplication."""
    from utils.hashing import compute_review_hash

    return compute_review_hash(
        place_id=place_id,
        author_display_name=author_display_name,
        rating=rating,
        original_text=original_text,
        publish_time=publish_time,
    )


# ---------------------------------------------------------------------------
# Collector class
# ---------------------------------------------------------------------------


class GoogleReviewsCollector(BaseCollector):
    """
    Fetches Google Places API reviews for matched kindergartens.

    Only processes places with match_status in AUTO_ACCEPT / MANUAL_REVIEW.
    Skips places already collected in the current run unless force=True.
    """

    @property
    def crawler_name(self) -> str:
        return "google_reviews"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def collect(self, **kwargs) -> CrawlResult:
        """
        Run the Google Reviews collection pipeline.

        Keyword args
        ------------
        district : str | None
            Limit collection to this district.
        limit : int | None
            Maximum number of places to process.
        dry_run : bool
            If True, do not write to any database.
        force : bool
            If True, re-collect even if reviews were already fetched.
        api_key : str | None
            Override API key from settings.
        """
        from utils.logging import setup_logging

        setup_logging()

        district_filter: Optional[str] = kwargs.get("district")
        limit: Optional[int] = kwargs.get("limit")
        dry_run: bool = bool(kwargs.get("dry_run", False))
        force: bool = bool(kwargs.get("force", False))

        self.log_start(district=district_filter)

        # ── API key check ─────────────────────────────────────────────
        api_key = kwargs.get("api_key") or self._get_api_key()
        if not api_key:
            logger.warning(
                "Google Maps API key not set. "
                "Set GOOGLE_MAPS_API_KEY in .env or pass api_key=. Skipping."
            )
            result = CrawlResult()
            result.errors.append("GOOGLE_MAPS_API_KEY not configured")
            self.log_finish(result, district=district_filter)
            return result

        # ── Initialise databases ──────────────────────────────────────
        from database.reviews_db import create_tables as create_reviews_tables
        from database.master_db import create_tables as create_master_tables

        create_reviews_tables()
        create_master_tables()

        # ── Record crawl start ────────────────────────────────────────
        run_start_ts = datetime.now(timezone.utc).isoformat()
        run_id: Optional[int] = None
        if not dry_run:
            try:
                from database.master_db import start_crawl_run

                run_id = start_crawl_run(
                    crawler=self.crawler_name,
                    district=district_filter,
                    started_at=run_start_ts,
                )
            except Exception as exc:
                logger.warning("Could not record crawl start: %s", exc)

        result = CrawlResult()

        # ── Load matched places ───────────────────────────────────────
        try:
            matched_places = self._load_matched_places(
                district=district_filter, limit=limit
            )
        except Exception as exc:
            logger.error("Failed to load matched places: %s", exc)
            result.errors.append(str(exc))
            self.log_finish(result, district=district_filter)
            return result

        logger.info("Processing reviews for %d matched places.", len(matched_places))

        from config.settings import settings

        request_delay = settings.GOOGLE_REQUEST_DELAY
        timeout = settings.GOOGLE_TIMEOUT

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for place_row in matched_places:
                result.total += 1
                place_id: str = place_row.get("google_place_id") or ""
                kg_id: int = place_row.get("kindergarten_id", 0)
                district: str = place_row.get("district", "")
                official_name: str = place_row.get("official_name", "")

                if not place_id:
                    logger.debug(
                        "Skipping kg_id=%d: no google_place_id.", kg_id
                    )
                    result.skipped += 1
                    continue

                # ── Resume check ──────────────────────────────────────
                if not force and not dry_run:
                    if self._already_collected(place_id, run_start_ts):
                        logger.debug(
                            "Skipping place_id=%s ('%s'): already collected.",
                            place_id,
                            official_name,
                        )
                        result.skipped += 1
                        continue

                try:
                    new_count, skip_count = self._process_place(
                        place_id=place_id,
                        kg_id=kg_id,
                        district=district,
                        official_name=official_name,
                        api_key=api_key,
                        client=client,
                        dry_run=dry_run,
                        timeout=timeout,
                    )
                    result.success += new_count
                    result.skipped += skip_count

                    matching_logger.info(
                        "REVIEWS district=%s name=%r place_id=%s "
                        "new=%d skipped=%d",
                        district, official_name, place_id, new_count, skip_count,
                    )

                    if request_delay > 0:
                        time.sleep(request_delay)

                except Exception as exc:
                    result.failed += 1
                    result.errors.append(f"place_id={place_id}: {exc}")
                    logger.error(
                        "Error collecting reviews for place_id=%s '%s': %s",
                        place_id,
                        official_name,
                        exc,
                        exc_info=True,
                    )

        # ── Record crawl finish ────────────────────────────────────────
        finished_at = datetime.now(timezone.utc).isoformat()
        if not dry_run and run_id is not None:
            try:
                from database.master_db import finish_crawl_run

                finish_crawl_run(
                    run_id=run_id,
                    finished_at=finished_at,
                    total=result.total,
                    success=result.success,
                    failed=result.failed,
                    skipped=result.skipped,
                    status="DONE" if not result.errors else "PARTIAL",
                    error="; ".join(result.errors[:5]) if result.errors else None,
                )
            except Exception as exc:
                logger.warning("Could not record crawl finish: %s", exc)

        self.log_finish(result, district=district_filter)
        return result

    # ------------------------------------------------------------------
    # Single-place processing
    # ------------------------------------------------------------------

    def _process_place(
        self,
        place_id: str,
        kg_id: int,
        district: str,
        official_name: str,
        api_key: str,
        client: httpx.Client,
        dry_run: bool,
        timeout: int,
    ) -> tuple[int, int]:
        """
        Fetch and persist reviews for a single place.

        Returns (new_count, skip_count).
        """
        raw_reviews = _fetch_with_retry(
            place_id=place_id,
            api_key=api_key,
            client=client,
            timeout=timeout,
        )

        now_ts = datetime.now(timezone.utc).isoformat()
        new_count = 0
        skip_count = 0

        for raw in raw_reviews:
            parsed = _parse_review(raw)
            rating = parsed["rating"]

            # Validate rating
            if not (1 <= rating <= 5):
                logger.debug(
                    "Skipping review with invalid rating=%s for place_id=%s",
                    rating,
                    place_id,
                )
                skip_count += 1
                continue

            content_hash = _compute_content_hash(
                place_id=place_id,
                author_display_name=parsed["author_display_name"],
                rating=rating,
                original_text=parsed["original_text"] or parsed["text"],
                publish_time=parsed["publish_time"],
            )

            review_record: dict[str, Any] = {
                "kindergarten_id": kg_id,
                "district": district,
                "place_id": place_id,
                "review_resource_name": parsed["review_resource_name"],
                "author_display_name": parsed["author_display_name"],
                "author_uri": parsed["author_uri"],
                "author_photo_uri": parsed["author_photo_uri"],
                "rating": rating,
                "text": parsed["text"] or None,
                "original_text": parsed["original_text"] or None,
                "text_language": parsed["text_language"] or None,
                "original_language": parsed["original_language"] or None,
                "publish_time": parsed["publish_time"] or None,
                "relative_publish_time": parsed["relative_publish_time"] or None,
                "review_uri": parsed["review_uri"] or None,
                "source": "GOOGLE_PLACES_API",
                "content_hash": content_hash,
                "first_seen_at": now_ts,
                "last_seen_at": now_ts,
                "raw_json": json.dumps(raw, ensure_ascii=False),
            }

            if dry_run:
                logger.debug(
                    "[dry_run] Would insert review hash=%s rating=%d",
                    content_hash[:12],
                    rating,
                )
                new_count += 1
                continue

            # ── Persist to reviews.db ─────────────────────────────────
            try:
                from database.reviews_db import insert_review_if_new

                inserted = insert_review_if_new(review_record)
                if inserted:
                    new_count += 1
                    # Save raw JSON file
                    self._save_raw_review_json(
                        district=district,
                        place_id=place_id,
                        content_hash=content_hash,
                        data=raw,
                    )
                else:
                    skip_count += 1
            except Exception as exc:
                logger.warning(
                    "Failed to save review hash=%s for place_id=%s: %s",
                    content_hash[:12],
                    place_id,
                    exc,
                )
                skip_count += 1
                continue

            # ── Mirror to master_db ───────────────────────────────────
            try:
                from database.master_db import get_connection as master_conn
                from database.master_db import reviews_table as master_reviews_table
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                master_record = {
                    k: v for k, v in review_record.items()
                    if k in {c.key for c in master_reviews_table.columns}
                }
                with master_conn() as conn:
                    stmt = (
                        sqlite_insert(master_reviews_table)
                        .values(**master_record)
                        .on_conflict_do_update(
                            index_elements=["content_hash"],
                            set_={"last_seen_at": now_ts},
                        )
                    )
                    conn.execute(stmt)
                    conn.commit()
            except Exception as exc:
                logger.warning(
                    "Failed to mirror review to master_db for place_id=%s: %s",
                    place_id,
                    exc,
                )

        return new_count, skip_count

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    @staticmethod
    def _get_api_key() -> str:
        try:
            from config.settings import settings

            return settings.GOOGLE_MAPS_API_KEY or ""
        except Exception:
            import os

            return os.environ.get("GOOGLE_MAPS_API_KEY", "")

    @staticmethod
    def _load_matched_places(
        district: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        Load place_match records whose status qualifies for review collection.
        """
        from database.places_db import get_connection, place_matches_table
        from sqlalchemy import select as sa_select

        with get_connection() as conn:
            stmt = sa_select(place_matches_table).where(
                place_matches_table.c.match_status.in_(list(_ELIGIBLE_STATUSES))
            )
            if district:
                stmt = stmt.where(place_matches_table.c.district == district)
            stmt = stmt.order_by(
                place_matches_table.c.district,
                place_matches_table.c.official_name,
            )
            if limit:
                stmt = stmt.limit(limit)
            rows = conn.execute(stmt).mappings().all()
            return [dict(r) for r in rows]

    @staticmethod
    def _already_collected(place_id: str, since_ts: str) -> bool:
        """
        Return True if reviews for this place were already collected (i.e., at
        least one review row exists with last_seen_at >= since_ts).

        This is the resume check — if we already ran this place in the current
        session, skip it.
        """
        try:
            from database.reviews_db import get_connection, reviews_table
            from sqlalchemy import select as sa_select

            with get_connection() as conn:
                row = conn.execute(
                    sa_select(reviews_table.c.id).where(
                        (reviews_table.c.place_id == place_id)
                        & (reviews_table.c.last_seen_at >= since_ts)
                    ).limit(1)
                ).first()
            return row is not None
        except Exception:
            return False

    @staticmethod
    def _save_raw_review_json(
        district: str,
        place_id: str,
        content_hash: str,
        data: dict,
    ) -> None:
        """
        Save a single raw review to
        data/raw/google/{district}/{place_id}_{hash[:8]}.json
        """
        try:
            from config.settings import settings

            out_dir: Path = settings.RAW_DIR / "google" / district
            out_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{place_id}_review_{content_hash[:8]}.json"
            out_path = out_dir / filename
            out_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug(
                "Failed to save raw review JSON for place_id=%s hash=%s: %s",
                place_id,
                content_hash[:8],
                exc,
            )
