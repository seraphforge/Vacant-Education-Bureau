"""
tests/test_place_matcher.py
----------------------------
Tests for analysis.place_matcher — scoring Google Places candidates against
official kindergarten records.
"""

import pytest
from analysis.place_matcher import (
    score_name,
    score_address,
    score_geo,
    calculate_match_score,
    determine_match_status,
    find_best_match,
    MatchResult,
)


class TestScoreName:
    """Tests for score_name()."""

    def test_exact_match(self):
        """Identical names → score close to 1.0."""
        score = score_name("汐止國民小學附設幼兒園", "汐止國民小學附設幼兒園")
        assert score >= 0.95, f"Expected >= 0.95, got {score}"

    def test_similar_names(self):
        """汐止國民小學附設幼兒園 vs 汐止國小附設幼兒園 — both are abbreviations of the
        same school; score should be > 0.7."""
        score = score_name("汐止國民小學附設幼兒園", "汐止國小附設幼兒園")
        assert score > 0.7, f"Expected > 0.7, got {score}"

    def test_very_different_names(self):
        """Completely different school names → score < 0.5."""
        score = score_name("汐止國小幼兒園", "板橋夢幼幼兒園")
        assert score < 0.5, f"Expected < 0.5, got {score}"

    def test_empty_official_name_returns_zero(self):
        """Empty official name → 0.0."""
        assert score_name("", "汐止幼兒園") == 0.0

    def test_empty_google_name_returns_zero(self):
        """Empty google name → 0.0."""
        assert score_name("汐止幼兒園", "") == 0.0

    def test_both_empty_returns_zero(self):
        assert score_name("", "") == 0.0

    def test_score_within_0_to_1(self):
        """Score is always in [0, 1]."""
        score = score_name("新北市立板橋幼兒園", "板橋市立幼兒園")
        assert 0.0 <= score <= 1.0


class TestScoreGeo:
    """Tests for score_geo()."""

    def test_close_coordinates_returns_1(self):
        """Within 0.5 km → 1.0."""
        # ~200m apart
        score = score_geo(25.0500, 121.5000, 25.0518, 121.5000)
        assert score == 1.0

    def test_far_coordinates_returns_0(self):
        """More than 5 km apart → 0.0."""
        # 汐止 vs 板橋 — well over 5 km
        score = score_geo(25.0680, 121.6600, 25.0130, 121.4630)
        assert score == 0.0

    def test_no_coordinates_returns_neutral(self):
        """Any None coordinate → 0.5 (neutral)."""
        assert score_geo(None, None, None, None) == 0.5
        assert score_geo(25.0, 121.5, None, None) == 0.5
        assert score_geo(None, None, 25.0, 121.5) == 0.5

    def test_medium_distance(self):
        """1–2 km → 0.5."""
        # ~1.5 km apart
        score = score_geo(25.0500, 121.5000, 25.0635, 121.5000)
        assert score == 0.5

    def test_near_distance(self):
        """0.5–1 km → 0.8."""
        # ~0.7 km apart
        score = score_geo(25.0500, 121.5000, 25.0563, 121.5000)
        assert score == 0.8


class TestCalculateMatchScore:
    """Tests for calculate_match_score()."""

    def test_weighted_formula(self):
        """name*0.5 + address*0.35 + geo*0.15 = expected composite."""
        name_s = 0.9
        addr_s = 0.8
        geo_s = 0.7
        expected = name_s * 0.50 + addr_s * 0.35 + geo_s * 0.15
        # 0.455 + 0.280 + 0.105 = 0.835
        assert abs(expected - 0.835) < 1e-9
        result = calculate_match_score(name_s, addr_s, geo_s)
        assert abs(result - expected) < 1e-9

    def test_all_zeros(self):
        assert calculate_match_score(0.0, 0.0, 0.0) == 0.0

    def test_all_ones(self):
        assert calculate_match_score(1.0, 1.0, 1.0) == pytest.approx(1.0)

    def test_weights_sum_to_one(self):
        """0.50 + 0.35 + 0.15 == 1.0, so max score is 1.0."""
        assert calculate_match_score(1.0, 1.0, 1.0) == pytest.approx(1.0)


class TestDetermineMatchStatus:
    """Tests for determine_match_status()."""

    def test_auto_accept_with_district_match(self):
        """score >= 0.88 AND district in google_address → AUTO_ACCEPT."""
        status, district_match = determine_match_status(
            match_score=0.92,
            official_district="汐止區",
            google_address="新北市汐止區大同路298號",
        )
        assert status == "AUTO_ACCEPT"
        assert district_match is True

    def test_auto_accept_requires_district_match(self):
        """High score but district NOT in address → not AUTO_ACCEPT."""
        status, district_match = determine_match_status(
            match_score=0.95,
            official_district="汐止區",
            google_address="新北市板橋區文化路180號",
        )
        # District mismatch overrides; score >= 0.50 so DISTRICT_MISMATCH
        assert status == "DISTRICT_MISMATCH"
        assert district_match is False

    def test_manual_review_status(self):
        """score in [0.72, 0.88) with district match → MANUAL_REVIEW."""
        status, district_match = determine_match_status(
            match_score=0.75,
            official_district="板橋區",
            google_address="新北市板橋區文化路180號",
        )
        assert status == "MANUAL_REVIEW"
        assert district_match is True

    def test_rejected_status(self):
        """score < 0.72 and district not matching → REJECTED."""
        status, district_match = determine_match_status(
            match_score=0.50,
            official_district="汐止區",
            google_address="新北市汐止區大同路",
        )
        # score 0.50 >= 0.50 but district matches → check logic:
        # district IS in google_address, so no DISTRICT_MISMATCH;
        # score < 0.72, not >= auto_accept → MANUAL_REVIEW threshold not met → REJECTED
        assert status == "REJECTED"

    def test_rejected_low_score(self):
        """Clearly low score → REJECTED."""
        status, _ = determine_match_status(
            match_score=0.30,
            official_district="板橋區",
            google_address="新北市板橋區某路",
        )
        assert status == "REJECTED"

    def test_district_mismatch(self):
        """official_district not in google_address, score >= 0.50 → DISTRICT_MISMATCH."""
        status, district_match = determine_match_status(
            match_score=0.80,
            official_district="汐止區",
            google_address="新北市板橋區文化路180號",
        )
        assert status == "DISTRICT_MISMATCH"
        assert district_match is False

    def test_custom_thresholds(self):
        """Custom auto_accept_threshold and manual_review_threshold are respected."""
        status, _ = determine_match_status(
            match_score=0.85,
            official_district="汐止區",
            google_address="新北市汐止區大同路",
            auto_accept_threshold=0.80,
            manual_review_threshold=0.60,
        )
        assert status == "AUTO_ACCEPT"


class TestFindBestMatch:
    """Tests for find_best_match()."""

    def test_returns_none_when_no_candidates(self):
        """Empty candidate list → None."""
        result = find_best_match(
            official_name="汐止國小附設幼兒園",
            official_address="新北市汐止區大同路298號",
            official_district="汐止區",
            official_lat=25.068,
            official_lng=121.660,
            candidates=[],
        )
        assert result is None

    def test_returns_best_candidate(self):
        """Returns the highest-scoring candidate from a list."""
        candidates = [
            {
                "id": "ChIJgood",
                "displayName": {"text": "汐止國小附設幼兒園"},
                "formattedAddress": "新北市汐止區大同路298號",
                "location": {"latitude": 25.0680, "longitude": 121.6600},
                "rating": 4.5,
                "userRatingCount": 12,
                "googleMapsUri": "https://maps.google.com/?cid=1",
                "businessStatus": "OPERATIONAL",
            },
            {
                "id": "ChIJbad",
                "displayName": {"text": "板橋夢幼幼兒園"},
                "formattedAddress": "新北市板橋區文化路180號",
                "location": {"latitude": 25.0130, "longitude": 121.4630},
                "rating": 3.0,
                "userRatingCount": 5,
                "googleMapsUri": "https://maps.google.com/?cid=2",
                "businessStatus": "OPERATIONAL",
            },
        ]
        result = find_best_match(
            official_name="汐止國小附設幼兒園",
            official_address="新北市汐止區大同路298號",
            official_district="汐止區",
            official_lat=25.0680,
            official_lng=121.6600,
            candidates=candidates,
        )
        # Should pick the matching Xizhi candidate
        assert result is not None
        assert result.google_place_id == "ChIJgood"

    def test_returns_none_when_best_score_below_threshold(self):
        """Returns None when even the best candidate is below manual_review_threshold."""
        candidates = [
            {
                "id": "ChIJpoor",
                "displayName": {"text": "完全不同的幼兒園"},
                "formattedAddress": "台北市信義區某路1號",
                "location": {"latitude": 25.0330, "longitude": 121.5654},
                "rating": None,
                "userRatingCount": None,
                "googleMapsUri": None,
                "businessStatus": "OPERATIONAL",
            }
        ]
        result = find_best_match(
            official_name="汐止國小附設幼兒園",
            official_address="新北市汐止區大同路298號",
            official_district="汐止區",
            official_lat=25.0680,
            official_lng=121.6600,
            candidates=candidates,
        )
        assert result is None

    def test_result_is_match_result_dataclass(self):
        """Returned object is a MatchResult dataclass."""
        candidates = [
            {
                "id": "ChIJtest",
                "displayName": {"text": "汐止國小附設幼兒園"},
                "formattedAddress": "新北市汐止區大同路298號",
                "location": {"latitude": 25.0680, "longitude": 121.6600},
                "rating": 4.5,
                "userRatingCount": 10,
                "googleMapsUri": "https://maps.google.com/?cid=3",
                "businessStatus": "OPERATIONAL",
            }
        ]
        result = find_best_match(
            official_name="汐止國小附設幼兒園",
            official_address="新北市汐止區大同路298號",
            official_district="汐止區",
            official_lat=25.0680,
            official_lng=121.6600,
            candidates=candidates,
        )
        assert result is not None
        assert isinstance(result, MatchResult)
        assert hasattr(result, "match_score")
        assert hasattr(result, "match_status")
        assert hasattr(result, "name_score")
        assert hasattr(result, "address_score")
        assert hasattr(result, "geo_score")
