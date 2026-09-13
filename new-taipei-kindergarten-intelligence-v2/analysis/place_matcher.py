"""
analysis/place_matcher.py
-------------------------
Scores Google Places API candidate places against official kindergarten records
to determine the best match.

Scoring model
-------------
  match_score = name_score * 0.50
              + address_score * 0.35
              + geo_score * 0.15

Status thresholds (default):
  AUTO_ACCEPT     score >= 0.88 AND district matches google_address
  MANUAL_REVIEW   score >= 0.72 (even if district is ambiguous)
  DISTRICT_MISMATCH  district NOT in google_address AND score >= 0.50
  REJECTED        score < 0.72 and does not meet district-mismatch rule
  NOT_FOUND       returned by find_best_match when best score < manual_review_threshold
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class MatchResult:
    """Scored match between an official kindergarten and a Google Place."""

    google_place_id: str
    google_name: str
    google_address: str
    google_latitude: Optional[float]
    google_longitude: Optional[float]
    google_rating: Optional[float]
    google_user_rating_count: Optional[int]
    google_maps_uri: Optional[str]
    business_status: Optional[str]
    name_score: float
    address_score: float
    geo_score: float
    match_score: float
    match_status: str  # AUTO_ACCEPT | MANUAL_REVIEW | REJECTED | DISTRICT_MISMATCH | NOT_FOUND
    district_match: bool


# ---------------------------------------------------------------------------
# Internal normalisation helpers
# ---------------------------------------------------------------------------

# Suffixes that can safely be stripped before fuzzy comparison.
_NAME_SUFFIXES: tuple[str, ...] = (
    "附設幼兒園",
    "附設幼稚園",
    "幼兒園",
    "幼稚園",
    "附幼",
)

# Prefixes/substrings to remove from official names to reduce noise.
_NAME_PREFIXES: tuple[str, ...] = (
    "新北市立",
    "新北市",
    "市立",
    "縣立",
    "公立",
)

_WHITESPACE_RE = re.compile(r"\s+")
_POSTAL_CODE_RE = re.compile(r"^\d{3,6}")


def _normalize_name(name: str) -> str:
    """Strip whitespace, common prefixes and suffixes from a kindergarten name."""
    if not name:
        return ""
    result = _WHITESPACE_RE.sub("", name.strip())
    # Remove leading administrative prefixes
    for prefix in _NAME_PREFIXES:
        result = result.replace(prefix, "")
    # Normalise legacy term to current term
    result = result.replace("幼稚園", "幼兒園")
    # Strip trailing type suffixes so "板橋國小附設幼兒園" → "板橋國小"
    for suffix in _NAME_SUFFIXES:
        if result.endswith(suffix):
            result = result[: -len(suffix)]
            break
    return result.strip()


def _normalize_address(address: str) -> str:
    """Strip postal code prefix and collapse whitespace."""
    if not address:
        return ""
    address = _WHITESPACE_RE.sub("", address.strip())
    address = _POSTAL_CODE_RE.sub("", address)
    return address.strip()


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------


def score_name(official_name: str, google_name: str) -> float:
    """Return a 0.0–1.0 name similarity score.

    Uses both ``token_sort_ratio`` (good for reordered tokens) and
    ``partial_ratio`` (good for substrings), then takes the maximum.
    Both names are normalised before comparison.

    Args:
        official_name: Official name from the government dataset.
        google_name:   Display name returned by the Google Places API.

    Returns:
        Similarity score in [0.0, 1.0].
    """
    norm_official = _normalize_name(official_name)
    norm_google = _normalize_name(google_name)

    if not norm_official or not norm_google:
        return 0.0

    token_sort = fuzz.token_sort_ratio(norm_official, norm_google) / 100.0
    partial = fuzz.partial_ratio(norm_official, norm_google) / 100.0

    # Also try WRatio which is a weighted combination of several strategies
    wratio = fuzz.WRatio(norm_official, norm_google) / 100.0

    return max(token_sort, partial, wratio)


def score_address(
    official_address: str,
    google_address: str,
    official_district: str,
) -> float:
    """Return a 0.0–1.0 address similarity score with district-awareness.

    Algorithm:
      1. Compute ``partial_ratio`` on normalised addresses → base_score.
      2. If ``official_district`` is present in ``google_address``:
         multiplier = 1.0 (no penalty / bonus already included via matching).
      3. If ``official_district`` is *not* present in ``google_address``:
         multiplier = 0.3 (heavy penalty — address is from the wrong district).

    Args:
        official_address: Official address from the government dataset.
        google_address:   Formatted address returned by the Google Places API.
        official_district: Expected district (e.g. "板橋區").

    Returns:
        Similarity score in [0.0, 1.0].
    """
    norm_official = _normalize_address(official_address)
    norm_google = _normalize_address(google_address)

    if not norm_official and not norm_google:
        return 0.5  # neutral when both are empty
    if not norm_official or not norm_google:
        return 0.0

    base_score = fuzz.partial_ratio(norm_official, norm_google) / 100.0

    district_in_google = official_district in google_address if official_district else True
    multiplier = 1.0 if district_in_google else 0.3

    return min(1.0, base_score * multiplier)


def score_geo(
    official_lat: Optional[float],
    official_lng: Optional[float],
    google_lat: Optional[float],
    google_lng: Optional[float],
) -> float:
    """Return a 0.0–1.0 geographic proximity score.

    Uses the Haversine formula to calculate distance in kilometres.

    Distance thresholds:
      ≤ 0.5 km  → 1.0
      ≤ 1.0 km  → 0.8
      ≤ 2.0 km  → 0.5
      ≤ 5.0 km  → 0.2
      > 5.0 km  → 0.0

    Returns 0.5 (neutral) when either set of coordinates is unavailable.

    Args:
        official_lat: Official record latitude (may be None).
        official_lng: Official record longitude (may be None).
        google_lat:   Google Places latitude (may be None).
        google_lng:   Google Places longitude (may be None).

    Returns:
        Proximity score in [0.0, 1.0].
    """
    if (
        official_lat is None
        or official_lng is None
        or google_lat is None
        or google_lng is None
    ):
        return 0.5  # neutral — no coordinates to compare

    # Haversine formula
    R = 6371.0  # Earth radius in km
    lat1 = math.radians(official_lat)
    lat2 = math.radians(google_lat)
    d_lat = math.radians(google_lat - official_lat)
    d_lng = math.radians(google_lng - official_lng)

    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance_km = R * c

    if distance_km <= 0.5:
        return 1.0
    elif distance_km <= 1.0:
        return 0.8
    elif distance_km <= 2.0:
        return 0.5
    elif distance_km <= 5.0:
        return 0.2
    else:
        return 0.0


def calculate_match_score(
    name_score: float,
    address_score: float,
    geo_score: float,
) -> float:
    """Compute the weighted composite match score.

    Weights: name 50%, address 35%, geo 15%.

    Args:
        name_score:    Score from :func:`score_name`.
        address_score: Score from :func:`score_address`.
        geo_score:     Score from :func:`score_geo`.

    Returns:
        Composite score in [0.0, 1.0].
    """
    return name_score * 0.50 + address_score * 0.35 + geo_score * 0.15


def determine_match_status(
    match_score: float,
    official_district: str,
    google_address: str,
    auto_accept_threshold: float = 0.88,
    manual_review_threshold: float = 0.72,
) -> tuple[str, bool]:
    """Classify a match result into a status string.

    Decision rules (evaluated in order):
      1. ``AUTO_ACCEPT``      if score ≥ auto_accept_threshold AND district found in google_address.
      2. ``DISTRICT_MISMATCH`` if district NOT in google_address AND score ≥ 0.50
         (score is plausible but wrong area — needs human review).
      3. ``MANUAL_REVIEW``    if score ≥ manual_review_threshold.
      4. ``REJECTED``         otherwise.

    Args:
        match_score:             Composite score from :func:`calculate_match_score`.
        official_district:       Expected district name (e.g. "板橋區").
        google_address:          Formatted address returned by Google Places.
        auto_accept_threshold:   Minimum score for automatic acceptance (default 0.88).
        manual_review_threshold: Minimum score for manual review queue (default 0.72).

    Returns:
        A ``(status_string, district_match_bool)`` tuple.
    """
    district_match = bool(official_district and official_district in google_address)

    if match_score >= auto_accept_threshold and district_match:
        return "AUTO_ACCEPT", district_match

    # District mismatch: confident on name/address similarity but wrong district
    if not district_match and match_score >= 0.50:
        return "DISTRICT_MISMATCH", district_match

    if match_score >= manual_review_threshold:
        return "MANUAL_REVIEW", district_match

    return "REJECTED", district_match


def score_candidate(
    official_name: str,
    official_address: str,
    official_district: str,
    official_lat: Optional[float],
    official_lng: Optional[float],
    candidate: dict,
) -> MatchResult:
    """Score a single Google Places candidate against an official kindergarten record.

    The candidate dict follows the Google Places API (New) response shape::

        {
            "id": "ChIJ...",
            "displayName": {"text": "板橋國小附設幼兒園"},
            "formattedAddress": "新北市板橋區...",
            "location": {"latitude": 25.01, "longitude": 121.46},
            "rating": 4.5,
            "userRatingCount": 12,
            "googleMapsUri": "https://maps.google.com/?cid=...",
            "businessStatus": "OPERATIONAL",
        }

    Args:
        official_name:     Official kindergarten name.
        official_address:  Official street address.
        official_district: Official district (e.g. "板橋區").
        official_lat:      Official latitude (may be None).
        official_lng:      Official longitude (may be None).
        candidate:         Google Places API place dict.

    Returns:
        A fully populated :class:`MatchResult`.
    """
    # --- Extract candidate fields -------------------------------------------
    place_id: str = candidate.get("id", "")

    display_name = candidate.get("displayName") or {}
    google_name: str = display_name.get("text", "") if isinstance(display_name, dict) else str(display_name)

    google_address: str = candidate.get("formattedAddress", "")

    location = candidate.get("location") or {}
    google_lat: Optional[float] = location.get("latitude") if isinstance(location, dict) else None
    google_lng: Optional[float] = location.get("longitude") if isinstance(location, dict) else None

    google_rating: Optional[float] = candidate.get("rating")
    if google_rating is not None:
        try:
            google_rating = float(google_rating)
        except (TypeError, ValueError):
            google_rating = None

    google_user_rating_count: Optional[int] = candidate.get("userRatingCount")
    if google_user_rating_count is not None:
        try:
            google_user_rating_count = int(google_user_rating_count)
        except (TypeError, ValueError):
            google_user_rating_count = None

    google_maps_uri: Optional[str] = candidate.get("googleMapsUri")
    business_status: Optional[str] = candidate.get("businessStatus")

    # --- Compute individual scores ------------------------------------------
    ns = score_name(official_name, google_name)
    as_ = score_address(official_address, google_address, official_district)
    gs = score_geo(official_lat, official_lng, google_lat, google_lng)
    ms = calculate_match_score(ns, as_, gs)

    status, district_match = determine_match_status(
        ms, official_district, google_address
    )

    return MatchResult(
        google_place_id=place_id,
        google_name=google_name,
        google_address=google_address,
        google_latitude=google_lat,
        google_longitude=google_lng,
        google_rating=google_rating,
        google_user_rating_count=google_user_rating_count,
        google_maps_uri=google_maps_uri,
        business_status=business_status,
        name_score=round(ns, 4),
        address_score=round(as_, 4),
        geo_score=round(gs, 4),
        match_score=round(ms, 4),
        match_status=status,
        district_match=district_match,
    )


def find_best_match(
    official_name: str,
    official_address: str,
    official_district: str,
    official_lat: Optional[float],
    official_lng: Optional[float],
    candidates: list[dict],
    auto_accept_threshold: float = 0.88,
    manual_review_threshold: float = 0.72,
) -> Optional[MatchResult]:
    """Find the highest-scoring Google Places candidate for an official kindergarten.

    Scores all candidates with :func:`score_candidate`, then returns the one
    with the highest ``match_score``.  If the best score is below
    ``manual_review_threshold`` the function returns ``None`` to signal that
    no usable match was found (NOT_FOUND).

    Args:
        official_name:           Official kindergarten name.
        official_address:        Official street address.
        official_district:       Official district (e.g. "板橋區").
        official_lat:            Official latitude (may be None).
        official_lng:            Official longitude (may be None).
        candidates:              List of Google Places API place dicts.
        auto_accept_threshold:   Forwarded to :func:`determine_match_status`.
        manual_review_threshold: Forwarded to :func:`determine_match_status`;
                                 also the minimum score to return a result.

    Returns:
        The best-scoring :class:`MatchResult`, or ``None`` if no candidate
        reaches ``manual_review_threshold``.
    """
    if not candidates:
        return None

    results: list[MatchResult] = []
    for candidate in candidates:
        result = score_candidate(
            official_name=official_name,
            official_address=official_address,
            official_district=official_district,
            official_lat=official_lat,
            official_lng=official_lng,
            candidate=candidate,
        )
        results.append(result)

    # Sort descending by match_score, break ties by name_score
    results.sort(key=lambda r: (r.match_score, r.name_score), reverse=True)
    best = results[0]

    if best.match_score < manual_review_threshold:
        return None  # NOT_FOUND — caller should log/record accordingly

    # Re-evaluate status using the caller-supplied thresholds (may differ from
    # defaults used inside score_candidate).
    status, district_match = determine_match_status(
        best.match_score,
        official_district,
        best.google_address,
        auto_accept_threshold=auto_accept_threshold,
        manual_review_threshold=manual_review_threshold,
    )
    best.match_status = status
    best.district_match = district_match

    return best
