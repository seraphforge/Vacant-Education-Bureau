"""
Normalization utilities for public/private classification and address parsing.

Core rule:
    Public classification MUST be derived from government data fields only.
    Google Maps data must never be used to determine is_public.
"""

from __future__ import annotations

import re
from typing import Optional

from database.models import PublicType


# ---------------------------------------------------------------------------
# Public type normalization
# ---------------------------------------------------------------------------

# Mapping from raw source field values to PublicType
# Order matters: more specific patterns first
_PUBLIC_TYPE_RULES: list[tuple[list[str], PublicType]] = [
    # Must-reject (checked before public keywords)
    (["私立"], PublicType.PRIVATE),
    (["準公共"], PublicType.QUASI_PUBLIC),
    (["非營利"], PublicType.NONPROFIT),
    (["公設民營"], PublicType.PUBLIC_PRIVATE_PARTNERSHIP),
    # Public variants
    (["國立"], PublicType.PUBLIC),
    (["縣立"], PublicType.PUBLIC),
    (["市立"], PublicType.PUBLIC),
    (["區立"], PublicType.PUBLIC),
    (["公立附設", "國民小學附設", "國小附設", "學校附設"], PublicType.PUBLIC_ATTACHED),
    (["公立"], PublicType.PUBLIC),
]


def normalize_public_type(raw_value: Optional[str]) -> PublicType:
    """
    Map a raw government data field value to a canonical PublicType.

    Decision rules:
    1. If raw_value is empty/None -> UNKNOWN
    2. Match against keyword list in priority order
    3. No match -> UNKNOWN

    Args:
        raw_value: Raw string from government dataset's 公私立/設立別/類型 field.

    Returns:
        Canonical PublicType.

    Examples:
        >>> normalize_public_type("公立")
        <PublicType.PUBLIC: 'public'>
        >>> normalize_public_type("私立")
        <PublicType.PRIVATE: 'private'>
        >>> normalize_public_type("準公共")
        <PublicType.QUASI_PUBLIC: 'quasi_public'>
        >>> normalize_public_type(None)
        <PublicType.UNKNOWN: 'unknown'>
    """
    if not raw_value or not raw_value.strip():
        return PublicType.UNKNOWN

    val = raw_value.strip()

    for keywords, pub_type in _PUBLIC_TYPE_RULES:
        for kw in keywords:
            if kw in val:
                return pub_type

    return PublicType.UNKNOWN


def is_truly_public(public_type: PublicType) -> bool:
    """
    Return True only for types that qualify as public under Taiwan education law.

    Explicitly False for:
    - PRIVATE
    - QUASI_PUBLIC (準公共: private kindergarten under government subsidy contract)
    - NONPROFIT (非營利)
    - PUBLIC_PRIVATE_PARTNERSHIP (公設民營)
    - UNKNOWN (when in doubt, exclude)

    Args:
        public_type: Normalized PublicType.

    Returns:
        True if the kindergarten is considered public; False otherwise.
    """
    return public_type in (PublicType.PUBLIC, PublicType.PUBLIC_ATTACHED)


def get_rejection_reason(public_type: PublicType) -> Optional[str]:
    """
    Return a rejection reason string for non-public types.

    Returns None if the type is public (should not be rejected).
    """
    mapping = {
        PublicType.PRIVATE: "private",
        PublicType.QUASI_PUBLIC: "quasi_public",
        PublicType.NONPROFIT: "nonprofit",
        PublicType.PUBLIC_PRIVATE_PARTNERSHIP: "public_private_partnership",
        PublicType.UNKNOWN: "unknown_type",
    }
    return mapping.get(public_type)


# ---------------------------------------------------------------------------
# Address normalization
# ---------------------------------------------------------------------------

_DISTRICT_PATTERN = re.compile(
    r"新北市\s*([^\s區]+區)"
)
_DISTRICT_ONLY_PATTERN = re.compile(
    r"([^\s市縣]+(?:區|鄉|鎮|市))"
)


def extract_district(address: Optional[str]) -> Optional[str]:
    """
    Extract the district (行政區) from a Taiwan address string.

    Handles formats like:
        "新北市板橋區文化路..."  -> "板橋區"
        "板橋區文化路..."       -> "板橋區"
        None / empty            -> None

    Args:
        address: Full address string.

    Returns:
        District string (e.g. "板橋區") or None.
    """
    if not address:
        return None

    # Try full New Taipei City format first
    m = _DISTRICT_PATTERN.search(address)
    if m:
        return m.group(1)

    # Try extracting district from partial address
    m = _DISTRICT_ONLY_PATTERN.search(address)
    if m:
        return m.group(1)

    return None


def normalize_address(address: Optional[str]) -> Optional[str]:
    """
    Normalize a Taiwan address string.

    - Strip leading/trailing whitespace
    - Replace full-width spaces
    - Ensure "新北市" prefix if address contains a recognized district

    Args:
        address: Raw address string.

    Returns:
        Cleaned address string, or None if empty.
    """
    if not address:
        return None
    # Normalize full-width characters
    addr = address.strip()
    addr = addr.replace("　", " ")  # full-width space -> half-width
    addr = re.sub(r"\s+", "", addr)  # remove all internal spaces for Chinese addresses
    return addr if addr else None


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    Normalize a Taiwan phone number string.

    Args:
        phone: Raw phone string.

    Returns:
        Cleaned phone string, or None if empty.
    """
    if not phone:
        return None
    phone = phone.strip()
    # Remove non-numeric except - and ()
    phone = re.sub(r"[^\d\-\(\)\+]", "", phone)
    return phone if phone else None


def build_official_name(
    school_name: Optional[str],
    kindergarten_name: Optional[str],
    district: Optional[str] = None,
) -> str:
    """
    Build a normalized official name for a kindergarten.

    Priority:
    1. If kindergarten_name already contains the school name -> use kindergarten_name
    2. Otherwise: school_name + kindergarten_name
    3. Fallback: kindergarten_name or school_name

    Args:
        school_name: Parent school name (e.g. "XX國民小學").
        kindergarten_name: Kindergarten-specific name (e.g. "附設幼兒園").
        district: District string for disambiguation (optional).

    Returns:
        Normalized official name string.
    """
    if not school_name and not kindergarten_name:
        return ""
    if not school_name:
        return kindergarten_name or ""
    if not kindergarten_name:
        return school_name
    # If kindergarten_name already contains the school name, don't duplicate
    if school_name in kindergarten_name:
        return kindergarten_name
    # Combine
    return f"{school_name}{kindergarten_name}"
