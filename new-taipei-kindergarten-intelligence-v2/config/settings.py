from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

class Settings(BaseSettings):
    # Google Maps API
    GOOGLE_MAPS_API_KEY: str = ""
    GOOGLE_REQUEST_DELAY: float = 0.7
    GOOGLE_MAX_RETRIES: int = 5
    GOOGLE_TIMEOUT: int = 30
    
    # Database paths
    DB_DIR: Path = BASE_DIR / "data" / "db"
    OFFICIAL_DB: Path = BASE_DIR / "data" / "db" / "official_kindergartens.db"
    PLACES_DB: Path = BASE_DIR / "data" / "db" / "google_places.db"
    REVIEWS_DB: Path = BASE_DIR / "data" / "db" / "reviews.db"
    MASTER_DB: Path = BASE_DIR / "data" / "db" / "kindergarten_reviews_master.db"
    
    # Data paths
    RAW_DIR: Path = BASE_DIR / "data" / "raw"
    REJECTED_DIR: Path = BASE_DIR / "data" / "rejected"
    EXPORTS_DIR: Path = BASE_DIR / "exports"
    LOGS_DIR: Path = BASE_DIR / "logs"
    
    # Matching thresholds
    MATCH_AUTO_ACCEPT: float = 0.88
    MATCH_MANUAL_REVIEW: float = 0.72
    
    # Weights
    NAME_WEIGHT: float = 0.50
    ADDRESS_WEIGHT: float = 0.35
    GEO_WEIGHT: float = 0.15
    
    # Official data source
    OFFICIAL_DATA_URL: str = "https://data.ntpc.gov.tw/api/datasets/b4b7ad3f-71b5-4ce5-8024-30d5f03d31e0/json"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
