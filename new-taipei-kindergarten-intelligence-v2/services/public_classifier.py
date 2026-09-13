"""
public_classifier.py
--------------------
Classifies kindergartens as PUBLIC, PRIVATE, QUASI_PUBLIC, NONPROFIT, or UNKNOWN
based on official data fields (kindergarten_type, public_type) and name strings.

Only PUBLIC kindergartens proceed to the Google Maps pipeline.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class PublicType(str, Enum):
    """Classification of a kindergarten's public/private status."""

    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"
    QUASI_PUBLIC = "QUASI_PUBLIC"
    NONPROFIT = "NONPROFIT"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Keyword sets — order matters when checking; more-specific checks first.
# ---------------------------------------------------------------------------

# These keywords in the name unambiguously signal a nonprofit arrangement.
_NONPROFIT_KEYWORDS: tuple[str, ...] = (
    "非營利",
    "社區互助",
    "職場互助",
)

# Quasi-public (準公共) mechanism — government-subsidised private providers.
_QUASI_PUBLIC_KEYWORDS: tuple[str, ...] = ("準公共",)

# Government-run public schools / attached kindergartens.
_PUBLIC_KEYWORDS: tuple[str, ...] = (
    "市立",
    "公立",
    "國民小學附設",
    "國小附設",
    "縣立",
)

# Indicators that a school is privately operated.
_PRIVATE_KEYWORDS: tuple[str, ...] = (
    "私立",
    "財團法人",
    # 社團法人 is private *unless* it co-occurs with a nonprofit keyword.
    "社團法人",
    "教會",
    "基督教",
    "天主教",
    "佛教",
)

# Official field values used in government datasets.
# These map directly to PublicType without name-parsing.
_OFFICIAL_TYPE_MAP: dict[str, PublicType] = {
    # kindergarten_type values
    "公立": PublicType.PUBLIC,
    "私立": PublicType.PRIVATE,
    "非營利": PublicType.NONPROFIT,
    # public_type / subsidy-scheme values sometimes seen in open data
    "準公共": PublicType.QUASI_PUBLIC,
    "非營利幼兒園": PublicType.NONPROFIT,
    "社區互助式": PublicType.NONPROFIT,
    "職場互助式": PublicType.NONPROFIT,
    "公立幼兒園": PublicType.PUBLIC,
    "私立幼兒園": PublicType.PRIVATE,
}


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """Return True if *text* contains any keyword in *keywords*."""
    return any(kw in text for kw in keywords)


def classify_kindergarten(
    kindergarten_type: Optional[str] = None,
    public_type: Optional[str] = None,
    official_name: Optional[str] = None,
    school_name: Optional[str] = None,
) -> PublicType:
    """
    Classify a kindergarten into one of the PublicType categories.

    Classification priority
    -----------------------
    1. Explicit match on ``kindergarten_type`` or ``public_type`` from official
       data (fastest, most authoritative).
    2. Keyword scan of ``official_name`` and ``school_name`` combined text.
       - NONPROFIT keywords are checked *first* because a nonprofit may also
         carry 社團法人 in its name.
       - QUASI_PUBLIC next (準公共 is unambiguous).
       - PUBLIC next (government-run identifiers).
       - PRIVATE last (catches anything else that has private markers).
    3. UNKNOWN if nothing matches.

    Parameters
    ----------
    kindergarten_type:
        Value from the official dataset's type field (e.g. "公立", "私立").
    public_type:
        Value from the official dataset's subsidy/public-type field.
    official_name:
        Full official name as registered with the authority.
    school_name:
        Common / display name of the school.

    Returns
    -------
    PublicType
    """
    # --- Step 1: official structured fields -----------------------------------
    for field_value in (kindergarten_type, public_type):
        if field_value:
            normalised = field_value.strip()
            if normalised in _OFFICIAL_TYPE_MAP:
                result = _OFFICIAL_TYPE_MAP[normalised]
                logger.debug(
                    "Classified via official field '%s' -> %s", normalised, result
                )
                return result

    # --- Step 2: keyword scan on names ----------------------------------------
    combined = " ".join(
        part
        for part in (official_name, school_name)
        if part
    ).strip()

    if not combined:
        logger.debug("No name text available; returning UNKNOWN")
        return PublicType.UNKNOWN

    # 2a. NONPROFIT — checked before PRIVATE so that e.g.
    #     "社團法人非營利幼兒園" is classified as NONPROFIT not PRIVATE.
    if _contains_any(combined, _NONPROFIT_KEYWORDS):
        logger.debug("Classified via nonprofit keyword in '%s'", combined)
        return PublicType.NONPROFIT

    # 2b. QUASI_PUBLIC
    if _contains_any(combined, _QUASI_PUBLIC_KEYWORDS):
        logger.debug("Classified via quasi-public keyword in '%s'", combined)
        return PublicType.QUASI_PUBLIC

    # 2c. PUBLIC — must have a public keyword AND must NOT have private keywords.
    #     Rationale: "國小附設私立幼兒園" would be unusual but we default to PRIVATE
    #     if private markers are present.
    if _contains_any(combined, _PUBLIC_KEYWORDS):
        if not _contains_any(combined, _PRIVATE_KEYWORDS):
            logger.debug("Classified as PUBLIC via keyword in '%s'", combined)
            return PublicType.PUBLIC
        # Has both public and private markers — treat as PRIVATE (conservative).
        logger.warning(
            "Name '%s' has both public and private keywords; defaulting to PRIVATE",
            combined,
        )
        return PublicType.PRIVATE

    # 2d. PRIVATE
    if _contains_any(combined, _PRIVATE_KEYWORDS):
        logger.debug("Classified as PRIVATE via keyword in '%s'", combined)
        return PublicType.PRIVATE

    logger.debug("Could not classify '%s'; returning UNKNOWN", combined)
    return PublicType.UNKNOWN


def is_public(
    kindergarten_type: Optional[str] = None,
    public_type: Optional[str] = None,
    official_name: Optional[str] = None,
    school_name: Optional[str] = None,
) -> bool:
    """
    Convenience wrapper that returns True only for PUBLIC kindergartens.

    Only PUBLIC kindergartens go forward to the Google Maps pipeline.
    """
    return (
        classify_kindergarten(
            kindergarten_type=kindergarten_type,
            public_type=public_type,
            official_name=official_name,
            school_name=school_name,
        )
        == PublicType.PUBLIC
    )
