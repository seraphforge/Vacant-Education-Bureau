"""
district_resolver.py
--------------------
Resolves the administrative district (區) for a kindergarten record.

Resolution order
----------------
1. If the ``district`` field is already a valid New Taipei district string,
   use it as-is.
2. Otherwise, attempt to extract a district from the ``address`` field using
   ``utils.normalization.extract_district_from_address``.
3. If neither source yields a district, return ``"UNKNOWN"``.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# All 29 administrative districts of New Taipei City (新北市).
# Used to validate whether a candidate district string is actually valid.
# ---------------------------------------------------------------------------
NEW_TAIPEI_DISTRICTS: frozenset[str] = frozenset(
    [
        "板橋區",
        "新莊區",
        "中和區",
        "永和區",
        "土城區",
        "三重區",
        "蘆洲區",
        "汐止區",
        "新店區",
        "樹林區",
        "鶯歌區",
        "三峽區",
        "淡水區",
        "瑞芳區",
        "五股區",
        "泰山區",
        "林口區",
        "深坑區",
        "石碇區",
        "坪林區",
        "三芝區",
        "石門區",
        "八里區",
        "平溪區",
        "雙溪區",
        "貢寮區",
        "金山區",
        "萬里區",
        "烏來區",
    ]
)

UNKNOWN_DISTRICT = "UNKNOWN"


def _is_valid_district(value: Optional[str]) -> bool:
    """Return True if *value* is a recognised New Taipei district name."""
    if not value:
        return False
    stripped = value.strip()
    # Accept with or without trailing 區 so that plain "板橋" also passes.
    if stripped in NEW_TAIPEI_DISTRICTS:
        return True
    if not stripped.endswith("區") and (stripped + "區") in NEW_TAIPEI_DISTRICTS:
        return True
    return False


def _normalise_district(value: str) -> str:
    """Return the canonical district string (always ends in 區)."""
    stripped = value.strip()
    if stripped in NEW_TAIPEI_DISTRICTS:
        return stripped
    candidate = stripped + "區"
    if candidate in NEW_TAIPEI_DISTRICTS:
        return candidate
    return stripped  # Fallback — should not reach here after _is_valid_district check


def resolve_district(
    district: Optional[str] = None,
    address: Optional[str] = None,
) -> str:
    """
    Resolve the administrative district for a kindergarten.

    Parameters
    ----------
    district:
        District value as supplied in the official data record.  May be
        ``None``, an empty string, or an invalid/misspelled value.
    address:
        Full address string for the kindergarten.  Used as a fallback when
        *district* is not valid.

    Returns
    -------
    str
        A valid New Taipei district name (e.g. ``"板橋區"``) or
        ``"UNKNOWN"`` if the district cannot be determined.
    """
    # --- Step 1: validate the supplied district field -------------------------
    if _is_valid_district(district):
        resolved = _normalise_district(district)  # type: ignore[arg-type]
        logger.debug("District resolved from field: '%s'", resolved)
        return resolved

    # --- Step 2: parse from address -------------------------------------------
    if address:
        try:
            from utils.normalization import extract_district_from_address  # noqa: PLC0415

            extracted = extract_district_from_address(address)
            if extracted and _is_valid_district(extracted):
                resolved = _normalise_district(extracted)
                logger.debug(
                    "District extracted from address '%s': '%s'", address, resolved
                )
                return resolved
            if extracted:
                logger.debug(
                    "extract_district_from_address returned '%s' for address '%s', "
                    "but it is not a recognised New Taipei district.",
                    extracted,
                    address,
                )
        except ImportError:
            logger.warning(
                "utils.normalization is not available; cannot extract district "
                "from address '%s'.",
                address,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Unexpected error extracting district from address '%s': %s",
                address,
                exc,
            )

    # --- Step 3: give up ------------------------------------------------------
    logger.debug(
        "Could not resolve district (district=%r, address=%r); returning UNKNOWN.",
        district,
        address,
    )
    return UNKNOWN_DISTRICT
