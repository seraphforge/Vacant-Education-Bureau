import re
from typing import Optional

def normalize_name(name: str) -> str:
    """Normalize kindergarten name for matching."""
    if not name:
        return ""
    # Remove extra whitespace
    name = re.sub(r'\s+', '', name)
    # Common abbreviation expansions
    replacements = [
        (r'附設幼稚園', '附設幼兒園'),
        (r'幼稚園', '幼兒園'),
        (r'國民小學', '國小'),
        (r'新北市立', ''),
        (r'新北市', ''),
    ]
    for pattern, repl in replacements:
        name = re.sub(pattern, repl, name)
    return name.strip()

def normalize_address(address: str) -> str:
    """Normalize address for matching."""
    if not address:
        return ""
    address = re.sub(r'\s+', '', address)
    # Remove postal code prefix
    address = re.sub(r'^\d{3,6}', '', address)
    return address.strip()

def extract_district_from_address(address: str) -> Optional[str]:
    """Extract New Taipei district from address string."""
    if not address:
        return None
    from config.new_taipei_districts import NEW_TAIPEI_DISTRICTS_SET, DISTRICT_ALIASES
    # Try exact district match
    for district in NEW_TAIPEI_DISTRICTS_SET:
        if district in address:
            return district
    # Try alias match
    for alias, canonical in DISTRICT_ALIASES.items():
        if alias in address:
            return canonical
    return None

def is_new_taipei_address(address: str) -> bool:
    """Check if address belongs to New Taipei City."""
    if not address:
        return False
    return '新北市' in address or '新北' in address
