import hashlib
from typing import Optional

def compute_review_hash(
    place_id: str,
    author_display_name: str,
    rating: int,
    original_text: Optional[str],
    publish_time: Optional[str],
) -> str:
    """Compute SHA256 hash for review deduplication."""
    components = [
        str(place_id or ""),
        str(author_display_name or ""),
        str(rating or ""),
        str(original_text or ""),
        str(publish_time or ""),
    ]
    raw = "|".join(components)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def compute_kindergarten_hash(official_name: str, address: str) -> str:
    """Compute hash for kindergarten deduplication."""
    components = [
        str(official_name or "").strip(),
        str(address or "").strip(),
    ]
    raw = "|".join(components)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
