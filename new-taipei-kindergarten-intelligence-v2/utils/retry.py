import time
import logging
from typing import Callable, TypeVar, Any
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

def make_retry_decorator(max_retries: int = 5):
    """Create a retry decorator with exponential backoff."""
    return retry(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((
            httpx.HTTPStatusError,
            httpx.TimeoutException,
            httpx.NetworkError,
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )

class RateLimitError(Exception):
    """Raised when API rate limit is hit."""
    pass

class APIError(Exception):
    """Raised for non-retryable API errors."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"API Error {status_code}: {message}")

def handle_http_error(response: httpx.Response) -> None:
    """Raise appropriate exception based on HTTP status code."""
    if response.status_code == 200:
        return
    elif response.status_code == 429:
        raise RateLimitError(f"Rate limit exceeded: {response.text[:200]}")
    elif response.status_code in (400, 401, 403, 404):
        raise APIError(response.status_code, response.text[:200])
    elif response.status_code in (500, 502, 503, 504):
        raise httpx.HTTPStatusError(
            f"Server error {response.status_code}",
            request=response.request,
            response=response,
        )
    else:
        raise APIError(response.status_code, f"Unexpected status: {response.text[:200]}")
