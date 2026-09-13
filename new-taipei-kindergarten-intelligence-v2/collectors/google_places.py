"""
collectors/google_places.py
-----------------------------
Matches each official New Taipei public kindergarten to a Google Places entry
using the Places API (New) text-search endpoint.

Endpoint
--------
POST https://places.googleapis.com/v1/places:searchText
Headers:
    X-Goog-Api-Key: {API_KEY}
    X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,
                      places.location,places.rating,places.userRatingCount,
                      places.googleMapsUri,places.businessStatus

Search strategies (tried in order until score >= MATCH_THRESHOLD):
1. "{official_name} {district} 新北市"
2. "{official_name} {address}"
3. "{school_name} 附設幼兒園 {district}"
4. "{kindergarten_name} {district}"

Rate limiting
-------------
* Exponential back-off on 429 / 5xx.
* Configurable inter-request delay via settings.GOOGLE_REQUEST_DELAY.

Resume support
--------------
Kindergartens that already have a place_match record are skipped (unless the
existing record has status FAILED / NEEDS_RETRY).

Matching
--------
Delegates scoring to services.place_matcher (if available) or falls back to
a built-in rapidfuzz-based scorer.  Score threshold: 0.72.
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

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.location,"
    "places.rating,"
    "places.userRatingCount,"
    "places.googleMapsUri,"
    "places.businessStatus"
)

MATCH_THRESHOLD = 0.72         # minimum score to accept a candidate
AUTO_ACCEPT_THRESHOLD = 0.88   # auto-accept without manual review

_MAX_RETRIES = 5
_BACKOFF_BASE = 2.0            # seconds

# ---------------------------------------------------------------------------
# Inline fallback scorer (used when services.place_matcher is not available)
# ---------------------------------------------------------------------------


def _score_candidates(
    candidates: list[dict],
    official_name: str,
    address: str,
    district: str,
) -> list[tuple[float, dict]]:
    """
    Score each Google Places candidate against the official kindergarten data.

    Returns a list of (score, candidate) tuples sorted by score descending.

    Scoring breakdown (mirrors settings weights):
      - name_score  : 50 %
      - address_score: 35 %
      - geo_score   : 15 % (binary: district appears in address)
    """
    try:
        from rapidfuzz import fuzz

        def name_sim(a: str, b: str) -> float:
            return fuzz.token_set_ratio(a, b) / 100.0

        def addr_sim(a: str, b: str) -> float:
            return fuzz.partial_ratio(a, b) / 100.0

    except ImportError:
        # Minimal fallback if rapidfuzz is not installed
        def name_sim(a: str, b: str) -> float:  # type: ignore[misc]
            a_set = set(a)
            b_set = set(b)
            if not a_set or not b_set:
                return 0.0
            return len(a_set & b_set) / max(len(a_set), len(b_set))

        def addr_sim(a: str, b: str) -> float:  # type: ignore[misc]
            if not a or not b:
                return 0.0
            shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
            return 1.0 if shorter in longer else 0.0

    results: list[tuple[float, dict]] = []
    for candidate in candidates:
        g_name = candidate.get("displayName", {}).get("text", "")
        g_addr = candidate.get("formattedAddress", "")

        ns = name_sim(official_name, g_name)
        as_ = addr_sim(address, g_addr)

        # Geo score: 1.0 if district appears anywhere in the google address
        gs = 1.0 if district and district in g_addr else 0.0

        score = ns * 0.50 + as_ * 0.35 + gs * 0.15
        results.append((round(score, 4), candidate))

    results.sort(key=lambda x: x[0], reverse=True)
    return results


def _score_with_matcher(
    candidates: list[dict],
    official_name: str,
    address: str,
    district: str,
) -> list[tuple[float, dict, dict]]:
    """
    Try to use services.place_matcher for richer scoring.

    Returns list of (score, candidate, score_detail) tuples.
    Falls back to _score_candidates if place_matcher is unavailable.
    """
    try:
        from services import place_matcher  # type: ignore[import]

        scored = []
        for candidate in candidates:
            score_detail = place_matcher.score(
                official_name=official_name,
                official_address=address,
                district=district,
                google_place=candidate,
            )
            total = score_detail.get("total", 0.0)
            scored.append((round(total, 4), candidate, score_detail))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored
    except (ImportError, AttributeError):
        # place_matcher not yet implemented — use inline scorer
        basic = _score_candidates(candidates, official_name, address, district)
        return [(s, c, {"total": s, "name_score": s, "address_score": s, "geo_score": 0.0})
                for s, c in basic]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _search_places(
    query: str,
    api_key: str,
    client: httpx.Client,
    timeout: int = 30,
) -> list[dict]:
    """
    Execute one Places API text-search request.

    Returns the list of place dicts from the response, or [] on failure.
    Raises RateLimitError / APIError for non-retryable failures.
    """
    from utils.retry import RateLimitError, APIError, handle_http_error

    payload = {
        "textQuery": query,
        "languageCode": "zh-TW",
        "regionCode": "TW",
    }
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
        "Content-Type": "application/json",
    }

    response = client.post(
        PLACES_SEARCH_URL,
        json=payload,
        headers=headers,
        timeout=timeout,
    )

    handle_http_error(response)

    data = response.json()
    return data.get("places", [])


def _search_with_retry(
    query: str,
    api_key: str,
    client: httpx.Client,
    timeout: int = 30,
    max_retries: int = _MAX_RETRIES,
) -> list[dict]:
    """
    Wrap _search_places with exponential back-off retry on rate-limit /
    transient errors.
    """
    from utils.retry import RateLimitError, APIError

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            return _search_places(query, api_key, client, timeout=timeout)
        except RateLimitError as exc:
            last_exc = exc
            wait = _BACKOFF_BASE ** attempt
            logger.warning(
                "Rate limited on attempt %d/%d. Waiting %.1fs before retry.",
                attempt, max_retries, wait,
            )
            time.sleep(wait)
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "HTTP error on attempt %d/%d (%s). Retrying in %.1fs.",
                    attempt, max_retries, exc, wait,
                )
                time.sleep(wait)
            else:
                logger.error("All %d attempts failed for query: %r", max_retries, query)
        except Exception as exc:
            # Non-retryable
            logger.error("Non-retryable error for query %r: %s", query, exc)
            raise

    raise last_exc or RuntimeError(f"All retries exhausted for query: {query!r}")


# ---------------------------------------------------------------------------
# Collector class
# ---------------------------------------------------------------------------


class GooglePlacesCollector(BaseCollector):
    """
    Matches official kindergartens to Google Places entries.

    For each unmatched kindergarten:
      - Tries up to 4 search queries in priority order
      - Scores candidates using place_matcher (or inline scorer)
      - Saves the best match (if score >= 0.72) to google_places.db
      - Saves raw JSON to data/raw/google/{district}/{place_id}.json
    """

    @property
    def crawler_name(self) -> str:
        return "google_places"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def collect(self, **kwargs) -> CrawlResult:
        """
        Run the Google Places matching pipeline.

        Keyword args
        ------------
        district : str | None
            Limit collection to this district.
        limit : int | None
            Maximum number of kindergartens to process.
        dry_run : bool
            If True, do not write to any database.
        api_key : str | None
            Override the API key from settings.
        """
        from utils.logging import setup_logging

        setup_logging()

        district_filter: Optional[str] = kwargs.get("district")
        limit: Optional[int] = kwargs.get("limit")
        dry_run: bool = bool(kwargs.get("dry_run", False))

        self.log_start(district=district_filter)

        # ── API key ───────────────────────────────────────────────────
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
        from database.places_db import create_tables as create_places_tables
        from database.master_db import create_tables as create_master_tables

        create_places_tables()
        create_master_tables()

        # ── Record crawl start ────────────────────────────────────────
        started_at = datetime.now(timezone.utc).isoformat()
        run_id: Optional[int] = None
        if not dry_run:
            try:
                from database.master_db import start_crawl_run

                run_id = start_crawl_run(
                    crawler=self.crawler_name,
                    district=district_filter,
                    started_at=started_at,
                )
            except Exception as exc:
                logger.warning("Could not record crawl start: %s", exc)

        result = CrawlResult()

        # ── Load kindergartens to process ─────────────────────────────
        try:
            kindergartens = self._load_kindergartens(
                district=district_filter, limit=limit
            )
        except Exception as exc:
            logger.error("Failed to load kindergartens: %s", exc)
            result.errors.append(str(exc))
            self.log_finish(result, district=district_filter)
            return result

        logger.info("Processing %d kindergartens for Places matching.", len(kindergartens))

        from config.settings import settings

        request_delay = settings.GOOGLE_REQUEST_DELAY
        timeout = settings.GOOGLE_TIMEOUT

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for kg in kindergartens:
                result.total += 1
                kg_id: int = kg["id"]
                official_name: str = kg.get("official_name", "")
                district: str = kg.get("district", "")
                address: str = kg.get("address", "") or ""
                school_name: str = kg.get("school_name", "") or ""
                kindergarten_name: str = kg.get("kindergarten_name", "") or ""

                # ── Resume: skip if already matched ───────────────────
                if self._match_exists(kg_id) and not dry_run:
                    logger.debug(
                        "Skipping kg_id=%d '%s': match already exists.",
                        kg_id,
                        official_name,
                    )
                    result.skipped += 1
                    continue

                try:
                    outcome = self._process_kindergarten(
                        kg_id=kg_id,
                        official_name=official_name,
                        district=district,
                        address=address,
                        school_name=school_name,
                        kindergarten_name=kindergarten_name,
                        api_key=api_key,
                        client=client,
                        dry_run=dry_run,
                    )
                    if outcome == "success":
                        result.success += 1
                    elif outcome == "no_match":
                        result.failed += 1
                    elif outcome == "skipped":
                        result.skipped += 1

                    # Polite delay
                    if request_delay > 0:
                        time.sleep(request_delay)

                except Exception as exc:
                    result.failed += 1
                    result.errors.append(f"kg_id={kg_id}: {exc}")
                    logger.error(
                        "Unexpected error processing kg_id=%d '%s': %s",
                        kg_id,
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
    # Single-kindergarten processing
    # ------------------------------------------------------------------

    def _process_kindergarten(
        self,
        kg_id: int,
        official_name: str,
        district: str,
        address: str,
        school_name: str,
        kindergarten_name: str,
        api_key: str,
        client: httpx.Client,
        dry_run: bool,
    ) -> str:
        """
        Try all search strategies for one kindergarten.

        Returns "success" | "no_match" | "skipped".
        """
        from config.settings import settings

        strategies = self._build_queries(
            official_name=official_name,
            district=district,
            address=address,
            school_name=school_name,
            kindergarten_name=kindergarten_name,
        )

        best_score: float = 0.0
        best_candidate: Optional[dict] = None
        best_score_detail: dict = {}
        best_strategy_idx: int = -1

        for idx, query in enumerate(strategies):
            if not query.strip():
                continue

            logger.debug("Strategy %d query: %r", idx + 1, query)

            try:
                candidates = _search_with_retry(
                    query=query,
                    api_key=api_key,
                    client=client,
                    timeout=settings.GOOGLE_TIMEOUT,
                )
            except Exception as exc:
                logger.warning(
                    "Search strategy %d failed for '%s': %s", idx + 1, official_name, exc
                )
                continue

            if not candidates:
                logger.debug("Strategy %d returned 0 candidates.", idx + 1)
                continue

            scored = _score_with_matcher(candidates, official_name, address, district)
            if not scored:
                continue

            top_score, top_candidate, top_detail = scored[0]
            logger.debug(
                "Strategy %d best score=%.3f for '%s'",
                idx + 1,
                top_score,
                official_name,
            )

            if top_score > best_score:
                best_score = top_score
                best_candidate = top_candidate
                best_score_detail = top_detail
                best_strategy_idx = idx + 1

            if best_score >= MATCH_THRESHOLD:
                # Good enough — no need to try further strategies
                break

        # ── Determine match status ────────────────────────────────────
        if best_score >= AUTO_ACCEPT_THRESHOLD:
            match_status = "AUTO_ACCEPT"
        elif best_score >= MATCH_THRESHOLD:
            match_status = "MANUAL_REVIEW"
        else:
            # No acceptable match found
            matching_logger.info(
                "NO_MATCH district=%s name=%r best_score=%.3f",
                district,
                official_name,
                best_score,
            )

            if not dry_run:
                # Save a NO_MATCH record so we don't re-query on resume
                self._save_no_match(
                    kg_id=kg_id,
                    official_name=official_name,
                    district=district,
                    address=address,
                )
            return "no_match"

        # ── Extract place fields ──────────────────────────────────────
        assert best_candidate is not None  # guaranteed by score check above

        place_id: str = best_candidate.get("id", "")
        google_name: str = best_candidate.get("displayName", {}).get("text", "")
        google_address: str = best_candidate.get("formattedAddress", "")
        location: dict = best_candidate.get("location", {})
        google_lat: Optional[float] = location.get("latitude")
        google_lng: Optional[float] = location.get("longitude")
        google_rating: Optional[float] = best_candidate.get("rating")
        google_review_count: Optional[int] = best_candidate.get("userRatingCount")
        google_maps_uri: str = best_candidate.get("googleMapsUri", "")
        business_status: str = best_candidate.get("businessStatus", "")

        matching_logger.info(
            "MATCH district=%s name=%r google_name=%r score=%.3f "
            "strategy=%d status=%s place_id=%s",
            district,
            official_name,
            google_name,
            best_score,
            best_strategy_idx,
            match_status,
            place_id,
        )

        # ── Save raw JSON ─────────────────────────────────────────────
        if not dry_run and place_id:
            self._save_raw_json(
                district=district,
                place_id=place_id,
                data=best_candidate,
            )

        # ── Persist to places_db ──────────────────────────────────────
        now_ts = datetime.now(timezone.utc).isoformat()
        match_record: dict[str, Any] = {
            "kindergarten_id": kg_id,
            "official_name": official_name,
            "district": district,
            "official_address": address,
            "google_place_id": place_id,
            "google_name": google_name,
            "google_address": google_address,
            "google_latitude": google_lat,
            "google_longitude": google_lng,
            "google_rating": google_rating,
            "google_user_rating_count": google_review_count,
            "google_maps_uri": google_maps_uri,
            "business_status": business_status,
            "match_score": best_score,
            "name_score": best_score_detail.get("name_score"),
            "address_score": best_score_detail.get("address_score"),
            "geo_score": best_score_detail.get("geo_score"),
            "match_status": match_status,
            "match_method": f"strategy_{best_strategy_idx}",
            "raw_json": json.dumps(best_candidate, ensure_ascii=False),
            "collection_scope": "GOOGLE_PLACES_API_PARTIAL",
            "first_seen_at": now_ts,
            "last_seen_at": now_ts,
        }

        if not dry_run:
            try:
                from database.places_db import upsert_place_match

                upsert_place_match(match_record)
            except Exception as exc:
                logger.error(
                    "Failed to save place match for kg_id=%d: %s", kg_id, exc
                )
                return "no_match"

            # Mirror to master_db
            try:
                from database.master_db import get_connection as master_conn
                from database.master_db import places_table as master_places_table
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                with master_conn() as conn:
                    stmt = (
                        sqlite_insert(master_places_table)
                        .values(**{
                            k: v for k, v in match_record.items()
                            if k in {c.key for c in master_places_table.columns}
                        })
                        .on_conflict_do_update(
                            index_elements=["kindergarten_id", "google_place_id"],
                            set_={
                                k: v for k, v in match_record.items()
                                if k not in ("kindergarten_id", "google_place_id", "first_seen_at")
                                and k in {c.key for c in master_places_table.columns}
                            },
                        )
                    )
                    conn.execute(stmt)
                    conn.commit()
            except Exception as exc:
                logger.warning("Failed to mirror place match to master_db: %s", exc)

        return "success"

    # ------------------------------------------------------------------
    # Query builder
    # ------------------------------------------------------------------

    def _build_queries(
        self,
        official_name: str,
        district: str,
        address: str,
        school_name: str,
        kindergarten_name: str,
    ) -> list[str]:
        """
        Return the ordered list of search queries to try for a kindergarten.
        """
        queries = [
            f"{official_name} {district} 新北市",
            f"{official_name} {address}",
            f"{school_name} 附設幼兒園 {district}" if school_name else "",
            f"{kindergarten_name} {district}" if kindergarten_name else "",
        ]
        return queries

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
    def _load_kindergartens(
        district: Optional[str] = None, limit: Optional[int] = None
    ) -> list[dict]:
        """Load public kindergartens from official_db, optionally filtered."""
        from database.official_db import get_connection, kindergartens_table
        from sqlalchemy import select as sa_select

        with get_connection() as conn:
            stmt = sa_select(kindergartens_table).where(
                kindergartens_table.c.is_public == 1
            )
            if district:
                stmt = stmt.where(kindergartens_table.c.district == district)
            stmt = stmt.order_by(
                kindergartens_table.c.district,
                kindergartens_table.c.official_name,
            )
            if limit:
                stmt = stmt.limit(limit)
            rows = conn.execute(stmt).mappings().all()
            return [dict(r) for r in rows]

    @staticmethod
    def _match_exists(kg_id: int) -> bool:
        """Return True if a non-failed place match already exists for kg_id."""
        try:
            from database.places_db import get_connection, place_matches_table
            from sqlalchemy import select as sa_select

            with get_connection() as conn:
                row = conn.execute(
                    sa_select(place_matches_table.c.id).where(
                        (place_matches_table.c.kindergarten_id == kg_id)
                        & (place_matches_table.c.match_status.notin_(["FAILED", "NEEDS_RETRY"]))
                    )
                ).first()
            return row is not None
        except Exception:
            return False

    @staticmethod
    def _save_raw_json(district: str, place_id: str, data: dict) -> None:
        """Persist raw place JSON to data/raw/google/{district}/{place_id}.json."""
        try:
            from config.settings import settings

            out_dir: Path = settings.RAW_DIR / "google" / district
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{place_id}.json"
            out_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("Failed to save raw JSON for place_id=%s: %s", place_id, exc)

    @staticmethod
    def _save_no_match(
        kg_id: int,
        official_name: str,
        district: str,
        address: str,
    ) -> None:
        """
        Insert a NO_MATCH sentinel record so this kindergarten is skipped on
        subsequent resume runs.
        """
        try:
            from database.places_db import upsert_place_match

            now_ts = datetime.now(timezone.utc).isoformat()
            upsert_place_match({
                "kindergarten_id": kg_id,
                "official_name": official_name,
                "district": district,
                "official_address": address,
                "google_place_id": None,
                "match_score": 0.0,
                "match_status": "NO_MATCH",
                "match_method": "exhausted_strategies",
                "collection_scope": "GOOGLE_PLACES_API_PARTIAL",
                "first_seen_at": now_ts,
                "last_seen_at": now_ts,
            })
        except Exception as exc:
            logger.warning("Failed to save NO_MATCH record for kg_id=%d: %s", kg_id, exc)
