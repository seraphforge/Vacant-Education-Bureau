"""
Base class for all data collectors.

Provides common utilities:
- HTTP session with retry and backoff
- Raw response storage
- Rate limiting
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)


class CollectorError(Exception):
    """Raised when a collector encounters an unrecoverable error."""


class RateLimitError(CollectorError):
    """Raised on HTTP 429 Too Many Requests."""


class BaseCollector(ABC):
    """
    Abstract base for all data collectors in the pipeline.

    Subclasses must implement `collect()`.
    Provides save_raw() for storing raw API responses to disk.
    """

    def __init__(
        self,
        request_delay: float | None = None,
        max_retries: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self.request_delay = request_delay or settings.google_request_delay
        self.max_retries = max_retries or settings.google_max_retries
        self.timeout = timeout or settings.google_timeout
        self._last_request_time: float = 0.0

    @abstractmethod
    def collect(self, **kwargs: Any) -> Any:
        """Execute the collection. Must be implemented by subclasses."""

    def _throttle(self) -> None:
        """Enforce minimum delay between requests."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.monotonic()

    def save_raw(
        self,
        data: dict | list,
        subdir: str,
        filename: str,
        date: Optional[datetime] = None,
    ) -> Path:
        """
        Save a raw API response to disk for debugging.

        Path format: data/raw/{subdir}/{YYYY-MM-DD}/{filename}

        Args:
            data: JSON-serialisable dict or list.
            subdir: Sub-directory under data/raw/ (e.g. "google_places").
            filename: File name (e.g. "place_ChIJxxx.json").
            date: Date to use for directory name. Defaults to today.

        Returns:
            Path to the saved file.
        """
        date = date or datetime.utcnow()
        raw_dir = settings.raw_data_dir / subdir / date.strftime("%Y-%m-%d")
        raw_dir.mkdir(parents=True, exist_ok=True)
        file_path = raw_dir / filename
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return file_path

    def _make_http_client(self) -> httpx.Client:
        """Build an httpx Client with reasonable defaults."""
        return httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "NTKindergartenPipeline/1.0"},
        )
