"""
Hashing utilities for review deduplication.

Review content hash is based on:
    place_id + author_display_name + rating + original_text + publish_time

Using SHA-256 to ensure collision resistance.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional


def compute_review_hash(
    place_id: str,
    author_display_name: Optional[str],
    rating: Optional[int],
    original_text: Optional[str],
    publish_time: Optional[datetime],
) -> str:
    """
    Compute a SHA-256 content hash for a Google review.

    This hash is used to detect duplicate reviews across collection runs.
    If the same review is collected again, only last_seen_at is updated.

    Args:
        place_id: Google Place ID string.
        author_display_name: Reviewer's display name (may be None for anonymous).
        rating: Integer star rating 1-5 (may be None).
        original_text: Original language review text (may be None for rating-only reviews).
        publish_time: Review publish datetime (may be None).

    Returns:
        64-character lowercase hex SHA-256 digest.

    Example:
        >>> h = compute_review_hash("ChIJxxx", "王小明", 5, "很棒的幼兒園", datetime(2024,1,1))
        >>> len(h)
        64
    """
    # Canonicalize each component to a consistent string
    components = [
        str(place_id or ""),
        str(author_display_name or ""),
        str(rating or ""),
        str(original_text or ""),
        publish_time.isoformat() if publish_time else "",
    ]
    # Join with a separator unlikely to appear in content
    raw = "\x00".join(components)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_file_hash(content: bytes) -> str:
    """
    Compute a SHA-256 hash of arbitrary bytes (e.g., for raw API response files).

    Args:
        content: Raw bytes to hash.

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    return hashlib.sha256(content).hexdigest()
