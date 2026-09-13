"""
tests/test_district_resolver.py
--------------------------------
Tests for services.district_resolver — resolution of administrative districts
for New Taipei City kindergartens.
"""

import pytest
from services.district_resolver import resolve_district, NEW_TAIPEI_DISTRICTS

# All 29 New Taipei districts (same list as in the module under test)
ALL_29_DISTRICTS = [
    "板橋區", "新莊區", "中和區", "永和區", "土城區",
    "三重區", "蘆洲區", "汐止區", "新店區", "樹林區",
    "鶯歌區", "三峽區", "淡水區", "瑞芳區", "五股區",
    "泰山區", "林口區", "深坑區", "石碇區", "坪林區",
    "三芝區", "石門區", "八里區", "平溪區", "雙溪區",
    "貢寮區", "金山區", "萬里區", "烏來區",
]


class TestResolveDistrict:
    """Tests for resolve_district()."""

    def test_valid_district_passed_through(self):
        """A valid New Taipei district string is returned as-is."""
        assert resolve_district("汐止區", "") == "汐止區"

    def test_extract_from_address_xizhi(self):
        """汐止 is extracted from a full address when district field is empty."""
        result = resolve_district("", "新北市汐止區大同路298號")
        assert result == "汐止區"

    def test_extract_from_address_banqiao(self):
        """板橋 is extracted from a full address."""
        result = resolve_district("", "新北市板橋區文化路180號")
        assert result == "板橋區"

    def test_extract_from_address_xinzhuang(self):
        """新莊 is extracted from a full address."""
        result = resolve_district("", "新北市新莊區新莊路372號")
        assert result == "新莊區"

    def test_unknown_address_returns_unknown(self):
        """An address for a non-New-Taipei district returns UNKNOWN."""
        result = resolve_district("", "台北市中正區")
        assert result == "UNKNOWN"

    def test_empty_inputs_returns_unknown(self):
        """Both empty string inputs → UNKNOWN."""
        result = resolve_district("", "")
        assert result == "UNKNOWN"

    def test_none_inputs_returns_unknown(self):
        """Both None inputs → UNKNOWN."""
        result = resolve_district(None, None)
        assert result == "UNKNOWN"

    def test_all_29_districts_recognized(self):
        """Every one of the 29 New Taipei districts is recognised when passed
        directly as the district argument."""
        assert len(ALL_29_DISTRICTS) == 29
        for district in ALL_29_DISTRICTS:
            resolved = resolve_district(district, "")
            assert resolved == district, (
                f"Expected '{district}' to be passed through unchanged, "
                f"but got '{resolved}'"
            )

    def test_invalid_district_with_valid_address(self):
        """An invalid district field falls back to address extraction."""
        result = resolve_district("私立區", "新北市汐止區大同路")
        assert result == "汐止區"

    def test_district_field_takes_priority_over_address(self):
        """When district field is valid, it wins over the address."""
        result = resolve_district("板橋區", "新北市汐止區大同路298號")
        assert result == "板橋區"

    def test_module_constant_has_29_districts(self):
        """The NEW_TAIPEI_DISTRICTS frozenset exported by the module has 29 entries."""
        assert len(NEW_TAIPEI_DISTRICTS) == 29

    def test_淡水_district_recognised(self):
        """Verify a few more individual districts."""
        assert resolve_district("淡水區", "") == "淡水區"
        assert resolve_district("烏來區", "") == "烏來區"
        assert resolve_district("貢寮區", "") == "貢寮區"

    def test_extract_from_addresses_other_districts(self):
        """Address extraction for additional districts."""
        assert resolve_district("", "新北市淡水區中正路1號") == "淡水區"
        assert resolve_district("", "新北市三重區重新路100號") == "三重區"
        assert resolve_district("", "新北市土城區金城路200號") == "土城區"
