"""
Application settings loaded from environment variables.

All secrets and configurable values are read here.
No secret values are hardcoded anywhere in the codebase.

Usage:
    from config.settings import settings
    key = settings.google_maps_api_key
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root (if it exists)
_project_root = Path(__file__).resolve().parent.parent
_env_file = _project_root / ".env"
if _env_file.exists():
    load_dotenv(_env_file)


class Settings:
    """
    Central settings object. All values read from environment at import time.
    Raises ValueError on first access of required-but-missing secrets.
    """

    # --- Project paths ---
    project_root: Path = _project_root
    data_dir: Path = _project_root / "data"
    raw_data_dir: Path = _project_root / "data" / "raw"
    validation_dir: Path = _project_root / "data" / "validation"
    exports_dir: Path = _project_root / "exports"
    logs_dir: Path = _project_root / "logs"
    config_dir: Path = _project_root / "config"

    # --- Database ---
    @property
    def database_url(self) -> str:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            db_path = self.data_dir / "kindergarten_reviews.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{db_path}"
        return url

    # --- Google Maps API ---
    @property
    def google_maps_api_key(self) -> str:
        key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
        if not key:
            raise ValueError(
                "GOOGLE_MAPS_API_KEY is not set. "
                "Add it to your .env file or environment variables. "
                "See .env.example for reference."
            )
        return key

    @property
    def google_maps_api_key_optional(self) -> str | None:
        """Return API key or None (does not raise). Use for status checks."""
        return os.environ.get("GOOGLE_MAPS_API_KEY") or None

    # --- Google API rate limiting ---
    @property
    def google_request_delay(self) -> float:
        """Seconds to wait between Google API requests. Default 0.5."""
        return float(os.environ.get("GOOGLE_REQUEST_DELAY", "0.5"))

    @property
    def google_max_retries(self) -> int:
        """Maximum retry attempts on transient errors. Default 5."""
        return int(os.environ.get("GOOGLE_MAX_RETRIES", "5"))

    @property
    def google_timeout(self) -> float:
        """HTTP request timeout in seconds. Default 30."""
        return float(os.environ.get("GOOGLE_TIMEOUT", "30"))

    # --- Google Places API (New) ---
    GOOGLE_PLACES_BASE_URL: str = "https://places.googleapis.com/v1"
    GOOGLE_PLACES_SEARCH_URL: str = "https://places.googleapis.com/v1/places:searchText"
    GOOGLE_PLACES_DETAIL_URL: str = "https://places.googleapis.com/v1/places/{place_id}"

    # Field mask for Place Details request (reviews require specific fields)
    GOOGLE_PLACES_DETAIL_FIELDS: str = (
        "id,displayName,formattedAddress,location,"
        "rating,userRatingCount,googleMapsUri,businessStatus,reviews"
    )

    # --- Official data sources ---
    # Full Taiwan kindergarten open data from MOE (education ministry)
    # https://data.gov.tw/dataset/33018
    MOE_KINDERGARTEN_CSV_URL: str = (
        "https://data.moe.gov.tw/download/eduopendata20220501/kindergarten.csv"
    )

    # ECE MOE query page (full page load requires JavaScript)
    ECE_MOE_QUERY_URL: str = "https://www.ece.moe.edu.tw/ch/query-preschool/"

    # --- Validation thresholds ---
    GOOGLE_AUTO_ACCEPT_SCORE: float = 0.85
    GOOGLE_NEEDS_REVIEW_SCORE: float = 0.70

    # --- Matching weights ---
    MATCH_WEIGHT_NAME: float = 0.50
    MATCH_WEIGHT_ADDRESS: float = 0.30
    MATCH_WEIGHT_GEO: float = 0.20

    # --- Geographic constants ---
    NEW_TAIPEI_CITY: str = "新北市"
    NEW_TAIPEI_DISTRICTS: list[str] = [
        "板橋區", "三重區", "中和區", "永和區", "新莊區",
        "新店區", "樹林區", "鶯歌區", "三峽區", "淡水區",
        "汐止區", "瑞芳區", "土城區", "蘆洲區", "觀音山區",
        "泰山區", "林口區", "深坑區", "石碇區", "坪林區",
        "三芝區", "石門區", "八里區", "平溪區", "雙溪區",
        "貢寮區", "金山區", "萬里區", "烏來區",
    ]

    # --- Public type keywords (from government data) ---
    # Keywords that map to is_public=True
    PUBLIC_KEYWORDS: list[str] = [
        "公立", "市立", "區立", "國立", "縣立",
        "公立附設", "國民小學附設", "國小附設",
    ]
    # Keywords that MUST map to is_public=False
    PRIVATE_KEYWORDS: list[str] = ["私立"]
    QUASI_PUBLIC_KEYWORDS: list[str] = ["準公共"]
    NONPROFIT_KEYWORDS: list[str] = ["非營利"]
    PUBLIC_PRIVATE_KEYWORDS: list[str] = ["公設民營"]

    # --- Logging ---
    @property
    def log_level(self) -> str:
        return os.environ.get("LOG_LEVEL", "INFO").upper()

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "pipeline.log"

    # --- Place override file ---
    @property
    def place_overrides_file(self) -> Path:
        return self.config_dir / "place_overrides.csv"

    # --- Seed data file ---
    @property
    def seed_csv_file(self) -> Path:
        return self.data_dir / "public_kindergartens.csv"

    def ensure_dirs(self) -> None:
        """Create all required directories if they don't exist."""
        for d in [
            self.data_dir,
            self.raw_data_dir,
            self.validation_dir,
            self.exports_dir,
            self.logs_dir,
            self.raw_data_dir / "google_places",
        ]:
            d.mkdir(parents=True, exist_ok=True)


# Module-level singleton
settings = Settings()
