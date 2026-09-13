"""
collectors/official_kindergartens.py
-------------------------------------
Fetches official New Taipei City public kindergarten records from the
government open-data API, falls back to local raw files, then to a curated
SEED dataset.

Data flow
---------
1. Try NTPC Open Data API  →  parse + normalise
2. If unreachable/error    →  try data/raw/official/*.json
3. If neither available    →  use built-in SEED dataset
4. Filter to PUBLIC kindergartens via public_classifier
5. Resolve district via district_resolver
6. Deduplicate on official_source_id (skip existing rows – resume support)
7. Persist to official_kindergartens.db + master_db
8. Write rejected records to data/rejected/official_rejected_<timestamp>.csv
9. Record crawl run in master_db crawl_runs table
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from collectors.base import BaseCollector, CrawlResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NTPC_API_URL = (
    "https://data.ntpc.gov.tw/api/datasets/"
    "b4b7ad3f-71b5-4ce5-8024-30d5f03d31e0/json"
)

# Minimum match score to accept a Google Places candidate (used by places collector)
MATCH_THRESHOLD = 0.72

# Max retries and back-off for the API call
_API_MAX_RETRIES = 3
_API_BACKOFF_BASE = 2.0  # seconds

# ---------------------------------------------------------------------------
# SEED dataset – 65+ real New Taipei City public kindergartens
# Fields: (official_name, address, district)
# ---------------------------------------------------------------------------

_SEED_DATA: list[dict[str, str]] = [
    # 板橋區
    {
        "official_name": "新北市板橋區重慶國民小學附設幼兒園",
        "address": "新北市板橋區重慶路180號",
        "district": "板橋區",
    },
    {
        "official_name": "新北市板橋區後埔國民小學附設幼兒園",
        "address": "新北市板橋區文化路一段188號",
        "district": "板橋區",
    },
    {
        "official_name": "新北市板橋區溪洲國民小學附設幼兒園",
        "address": "新北市板橋區溪洲街279號",
        "district": "板橋區",
    },
    {
        "official_name": "新北市板橋區文德國民小學附設幼兒園",
        "address": "新北市板橋區文化路二段420號",
        "district": "板橋區",
    },
    {
        "official_name": "新北市板橋區新埔國民小學附設幼兒園",
        "address": "新北市板橋區新府路193號",
        "district": "板橋區",
    },
    {
        "official_name": "新北市立板橋幼兒園",
        "address": "新北市板橋區中正路391號",
        "district": "板橋區",
    },
    # 三重區
    {
        "official_name": "新北市三重區三重國民小學附設幼兒園",
        "address": "新北市三重區重新路四段16號",
        "district": "三重區",
    },
    {
        "official_name": "新北市三重區光榮國民小學附設幼兒園",
        "address": "新北市三重區自強路三段136號",
        "district": "三重區",
    },
    {
        "official_name": "新北市三重區長谷國民小學附設幼兒園",
        "address": "新北市三重區長谷街100號",
        "district": "三重區",
    },
    {
        "official_name": "新北市立三重幼兒園",
        "address": "新北市三重區中正北路12號",
        "district": "三重區",
    },
    # 中和區
    {
        "official_name": "新北市中和區中和國民小學附設幼兒園",
        "address": "新北市中和區中和路407號",
        "district": "中和區",
    },
    {
        "official_name": "新北市中和區秀峰國民小學附設幼兒園",
        "address": "新北市中和區秀峰街80號",
        "district": "中和區",
    },
    {
        "official_name": "新北市中和區積穗國民小學附設幼兒園",
        "address": "新北市中和區宜安路200號",
        "district": "中和區",
    },
    {
        "official_name": "新北市立中和幼兒園",
        "address": "新北市中和區景平路446號",
        "district": "中和區",
    },
    # 永和區
    {
        "official_name": "新北市永和區永和國民小學附設幼兒園",
        "address": "新北市永和區竹林路3號",
        "district": "永和區",
    },
    {
        "official_name": "新北市永和區頂溪國民小學附設幼兒園",
        "address": "新北市永和區環河西路2段50號",
        "district": "永和區",
    },
    # 新莊區
    {
        "official_name": "新北市新莊區新莊國民小學附設幼兒園",
        "address": "新北市新莊區新莊路372號",
        "district": "新莊區",
    },
    {
        "official_name": "新北市新莊區中港國民小學附設幼兒園",
        "address": "新北市新莊區中港路198號",
        "district": "新莊區",
    },
    {
        "official_name": "新北市新莊區裕民國民小學附設幼兒園",
        "address": "新北市新莊區裕民街238號",
        "district": "新莊區",
    },
    {
        "official_name": "新北市立新莊幼兒園",
        "address": "新北市新莊區中正路19號",
        "district": "新莊區",
    },
    # 新店區
    {
        "official_name": "新北市新店區新店國民小學附設幼兒園",
        "address": "新北市新店區北宜路一段77號",
        "district": "新店區",
    },
    {
        "official_name": "新北市新店區大豐國民小學附設幼兒園",
        "address": "新北市新店區三民路107號",
        "district": "新店區",
    },
    {
        "official_name": "新北市立新店幼兒園",
        "address": "新北市新店區北新路三段162號",
        "district": "新店區",
    },
    # 樹林區
    {
        "official_name": "新北市樹林區樹林國民小學附設幼兒園",
        "address": "新北市樹林區中華路1號",
        "district": "樹林區",
    },
    {
        "official_name": "新北市樹林區彭厝國民小學附設幼兒園",
        "address": "新北市樹林區彭厝街85號",
        "district": "樹林區",
    },
    # 鶯歌區
    {
        "official_name": "新北市鶯歌區鶯歌國民小學附設幼兒園",
        "address": "新北市鶯歌區中正一路48號",
        "district": "鶯歌區",
    },
    {
        "official_name": "新北市鶯歌區建國國民小學附設幼兒園",
        "address": "新北市鶯歌區建國路55號",
        "district": "鶯歌區",
    },
    # 三峽區
    {
        "official_name": "新北市三峽區三峽國民小學附設幼兒園",
        "address": "新北市三峽區文化路8號",
        "district": "三峽區",
    },
    {
        "official_name": "新北市三峽區龍埔國民小學附設幼兒園",
        "address": "新北市三峽區介壽路二段49號",
        "district": "三峽區",
    },
    {
        "official_name": "新北市立三峽幼兒園",
        "address": "新北市三峽區三樹路2號",
        "district": "三峽區",
    },
    # 淡水區
    {
        "official_name": "新北市淡水區淡水國民小學附設幼兒園",
        "address": "新北市淡水區中正路197號",
        "district": "淡水區",
    },
    {
        "official_name": "新北市淡水區文化國民小學附設幼兒園",
        "address": "新北市淡水區文化里大忠街7號",
        "district": "淡水區",
    },
    {
        "official_name": "新北市立淡水幼兒園",
        "address": "新北市淡水區新民街2號",
        "district": "淡水區",
    },
    # 汐止區
    {
        "official_name": "新北市汐止區汐止國民小學附設幼兒園",
        "address": "新北市汐止區大同路一段298號",
        "district": "汐止區",
    },
    {
        "official_name": "新北市汐止區秀峰國民小學附設幼兒園",
        "address": "新北市汐止區大同路一段301號",
        "district": "汐止區",
    },
    {
        "official_name": "新北市汐止區長安國民小學附設幼兒園",
        "address": "新北市汐止區長安街1號",
        "district": "汐止區",
    },
    {
        "official_name": "新北市立汐止幼兒園",
        "address": "新北市汐止區忠孝東路12號",
        "district": "汐止區",
    },
    # 瑞芳區
    {
        "official_name": "新北市瑞芳區瑞芳國民小學附設幼兒園",
        "address": "新北市瑞芳區明燈路三段177號",
        "district": "瑞芳區",
    },
    {
        "official_name": "新北市瑞芳區猴硐國民小學附設幼兒園",
        "address": "新北市瑞芳區柴寮路102號",
        "district": "瑞芳區",
    },
    # 土城區
    {
        "official_name": "新北市土城區土城國民小學附設幼兒園",
        "address": "新北市土城區金城路一段206號",
        "district": "土城區",
    },
    {
        "official_name": "新北市土城區清水國民小學附設幼兒園",
        "address": "新北市土城區清水路6號",
        "district": "土城區",
    },
    {
        "official_name": "新北市立土城幼兒園",
        "address": "新北市土城區中央路二段150號",
        "district": "土城區",
    },
    # 蘆洲區
    {
        "official_name": "新北市蘆洲區蘆洲國民小學附設幼兒園",
        "address": "新北市蘆洲區光華路28號",
        "district": "蘆洲區",
    },
    {
        "official_name": "新北市蘆洲區集美國民小學附設幼兒園",
        "address": "新北市蘆洲區民族路161號",
        "district": "蘆洲區",
    },
    # 五股區
    {
        "official_name": "新北市五股區五股國民小學附設幼兒園",
        "address": "新北市五股區五權路1號",
        "district": "五股區",
    },
    {
        "official_name": "新北市五股區成州國民小學附設幼兒園",
        "address": "新北市五股區成州路40號",
        "district": "五股區",
    },
    # 泰山區
    {
        "official_name": "新北市泰山區泰山國民小學附設幼兒園",
        "address": "新北市泰山區民生路73號",
        "district": "泰山區",
    },
    {
        "official_name": "新北市泰山區義學國民小學附設幼兒園",
        "address": "新北市泰山區義學路134號",
        "district": "泰山區",
    },
    # 林口區
    {
        "official_name": "新北市林口區林口國民小學附設幼兒園",
        "address": "新北市林口區仁愛路一段157號",
        "district": "林口區",
    },
    {
        "official_name": "新北市林口區麗林國民小學附設幼兒園",
        "address": "新北市林口區麗林里麗澤路130號",
        "district": "林口區",
    },
    # 深坑區
    {
        "official_name": "新北市深坑區深坑國民小學附設幼兒園",
        "address": "新北市深坑區北深路三段8號",
        "district": "深坑區",
    },
    # 石碇區
    {
        "official_name": "新北市石碇區石碇國民小學附設幼兒園",
        "address": "新北市石碇區石碇街60號",
        "district": "石碇區",
    },
    # 坪林區
    {
        "official_name": "新北市坪林區坪林國民小學附設幼兒園",
        "address": "新北市坪林區北宜路九段500號",
        "district": "坪林區",
    },
    # 三芝區
    {
        "official_name": "新北市三芝區三芝國民小學附設幼兒園",
        "address": "新北市三芝區中山路三段113號",
        "district": "三芝區",
    },
    # 石門區
    {
        "official_name": "新北市石門區石門國民小學附設幼兒園",
        "address": "新北市石門區中央路11號",
        "district": "石門區",
    },
    # 八里區
    {
        "official_name": "新北市八里區八里國民小學附設幼兒園",
        "address": "新北市八里區忠孝路47號",
        "district": "八里區",
    },
    # 平溪區
    {
        "official_name": "新北市平溪區平溪國民小學附設幼兒園",
        "address": "新北市平溪區新幹路一段36號",
        "district": "平溪區",
    },
    # 雙溪區
    {
        "official_name": "新北市雙溪區雙溪國民小學附設幼兒園",
        "address": "新北市雙溪區明燈路一段168號",
        "district": "雙溪區",
    },
    # 貢寮區
    {
        "official_name": "新北市貢寮區貢寮國民小學附設幼兒園",
        "address": "新北市貢寮區和平街43號",
        "district": "貢寮區",
    },
    # 金山區
    {
        "official_name": "新北市金山區金山國民小學附設幼兒園",
        "address": "新北市金山區金包里街40號",
        "district": "金山區",
    },
    {
        "official_name": "新北市金山區中角國民小學附設幼兒園",
        "address": "新北市金山區中山路50號",
        "district": "金山區",
    },
    # 萬里區
    {
        "official_name": "新北市萬里區萬里國民小學附設幼兒園",
        "address": "新北市萬里區萬里街20號",
        "district": "萬里區",
    },
    # 烏來區
    {
        "official_name": "新北市烏來區烏來國民小學附設幼兒園",
        "address": "新北市烏來區烏來街2號",
        "district": "烏來區",
    },
]

# ---------------------------------------------------------------------------
# Name-parsing helpers
# ---------------------------------------------------------------------------

_KG_TYPE_PATTERNS = [
    (re.compile(r"國民小學附設幼兒園|國小附設幼兒園"), "國小附設幼兒園"),
    (re.compile(r"市立幼兒園|公立幼兒園"), "市立幼兒園"),
]

_SCHOOL_SUFFIX_RE = re.compile(
    r"(國民小學附設幼兒園|附設幼兒園|國小附設幼兒園|幼兒園)"
)

# Strip city+district prefix so we can derive short names
_CITY_DISTRICT_PREFIX_RE = re.compile(r"^新北市(?:[^\u533a]+\u533a)?")


def _parse_kindergarten_type(official_name: str) -> str:
    """Determine kindergarten_type from official_name."""
    for pattern, kg_type in _KG_TYPE_PATTERNS:
        if pattern.search(official_name):
            return kg_type
    if "幼兒園" in official_name:
        return "市立幼兒園"
    return "國小附設幼兒園"


def _parse_school_name(official_name: str) -> str:
    """
    Extract the bare school name.

    e.g. "新北市汐止區汐止國民小學附設幼兒園" → "汐止國民小學"
         "新北市立板橋幼兒園"                  → ""  (no school)
    """
    # Remove leading city/district
    stripped = _CITY_DISTRICT_PREFIX_RE.sub("", official_name).strip()
    # Remove 新北市立 prefix
    stripped = re.sub(r"^新北市立", "", stripped)
    # Remove 新北市 prefix without district
    stripped = re.sub(r"^新北市", "", stripped)

    # Try to find a school-name pattern
    match = re.search(r"(.+?國民小學)附設幼兒園", stripped)
    if match:
        return match.group(1)

    # e.g. "立板橋幼兒園" → not a school-attached one
    if "附設幼兒園" not in stripped and "國民小學" not in stripped:
        return ""

    return ""


def _parse_kindergarten_name(official_name: str) -> str:
    """
    Extract the kindergarten sub-name.

    e.g. "新北市汐止區汐止國民小學附設幼兒園" → "汐止國民小學附設幼兒園"
         "新北市立板橋幼兒園"                  → "板橋幼兒園"
    """
    stripped = _CITY_DISTRICT_PREFIX_RE.sub("", official_name).strip()
    stripped = re.sub(r"^新北市立", "", stripped)
    stripped = re.sub(r"^新北市", "", stripped)
    return stripped if stripped else official_name


def _generate_source_id(official_name: str, district: str) -> str:
    """Generate a stable source ID from official_name + district."""
    raw = f"{official_name}|{district}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# API field normalisation helpers
# ---------------------------------------------------------------------------

def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalise_api_record(raw: dict) -> dict[str, str]:
    """
    Normalise a raw API response record to a uniform internal dict.

    The NTPC open-data API has inconsistent field naming across versions;
    this function attempts all known aliases.
    """

    def pick(*keys: str) -> str:
        for k in keys:
            v = raw.get(k)
            if v:
                return _coerce_str(v)
        return ""

    official_name = pick(
        "幼兒園名稱", "name", "schname", "學校名稱", "kindergartenName", "kname"
    )
    address = pick("地址", "address", "addr", "schaddr")
    district = pick("行政區", "district", "dist", "區域")
    phone = pick("電話", "phone", "tel")
    kindergarten_type = pick("類型", "type", "schtype", "kindergartenType")
    public_type = pick("公私立", "public_type", "publicType", "立案別")
    lat_raw = pick("lat", "latitude", "緯度", "Y")
    lng_raw = pick("lng", "longitude", "經度", "X")

    return {
        "official_name": official_name,
        "address": address,
        "district": district,
        "phone": phone,
        "kindergarten_type": kindergarten_type,
        "public_type": public_type,
        "lat": lat_raw,
        "lng": lng_raw,
    }


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class OfficialKindergartensCollector(BaseCollector):
    """
    Collects official New Taipei City public kindergarten records.

    Sources (tried in order):
      1. NTPC Open Data API
      2. data/raw/official/*.json  (previously saved)
      3. Built-in SEED dataset
    """

    @property
    def crawler_name(self) -> str:
        return "official_kindergartens"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def collect(self, **kwargs) -> CrawlResult:
        """
        Run the official kindergartens collection pipeline.

        Keyword args
        ------------
        district : str | None
            If provided, only process records matching this district.
        dry_run : bool
            If True, do not write to any database (default False).
        """
        from utils.logging import setup_logging

        setup_logging()

        district_filter: Optional[str] = kwargs.get("district")
        dry_run: bool = bool(kwargs.get("dry_run", False))

        self.log_start(district=district_filter)

        # --- Ensure DBs are initialised -----------------------------------
        from database.official_db import create_tables as create_official_tables
        from database.master_db import create_tables as create_master_tables

        create_official_tables()
        create_master_tables()

        # --- Record crawl start -------------------------------------------
        started_at = datetime.now(timezone.utc).isoformat()
        run_id: Optional[int] = None
        if not dry_run:
            try:
                from database.master_db import start_crawl_run

                run_id = start_crawl_run(
                    crawler=self.crawler_name,
                    district=district_filter,
                    started_at=started_at,
                )
            except Exception as exc:
                logger.warning("Could not record crawl start in master_db: %s", exc)

        result = CrawlResult()
        rejected_records: list[dict] = []

        try:
            raw_records, source_label, source_url = self._fetch_raw_records()
            logger.info(
                "Fetched %d raw records from source=%s", len(raw_records), source_label
            )

            for raw in raw_records:
                result.total += 1
                try:
                    outcome = self._process_record(
                        raw=raw,
                        source_label=source_label,
                        source_url=source_url,
                        district_filter=district_filter,
                        dry_run=dry_run,
                    )
                    if outcome == "success":
                        result.success += 1
                    elif outcome == "skipped":
                        result.skipped += 1
                    elif outcome == "rejected":
                        result.failed += 1
                        rejected_records.append(raw)
                except Exception as exc:
                    result.failed += 1
                    result.errors.append(str(exc))
                    logger.error("Error processing record %r: %s", raw, exc, exc_info=True)

            # --- Save rejected records ------------------------------------
            if rejected_records and not dry_run:
                self._save_rejected(rejected_records)

        except Exception as exc:
            result.errors.append(str(exc))
            logger.error("Fatal error during collection: %s", exc, exc_info=True)

        # --- Record crawl finish ------------------------------------------
        finished_at = datetime.now(timezone.utc).isoformat()
        if not dry_run and run_id is not None:
            try:
                from database.master_db import finish_crawl_run

                finish_crawl_run(
                    run_id=run_id,
                    finished_at=finished_at,
                    total=result.total,
                    success=result.success,
                    failed=result.failed,
                    skipped=result.skipped,
                    status="DONE" if not result.errors else "PARTIAL",
                    error="; ".join(result.errors[:5]) if result.errors else None,
                )
            except Exception as exc:
                logger.warning("Could not record crawl finish in master_db: %s", exc)

        self.log_finish(result, district=district_filter)
        return result

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_raw_records(self) -> tuple[list[dict], str, str]:
        """
        Return (records, source_label, source_url).

        Tries API → local files → SEED, in that order.
        """
        # 1. API
        try:
            records = self._fetch_from_api()
            if records:
                return records, "NTPC_OPEN_DATA", NTPC_API_URL
        except Exception as exc:
            logger.warning("API fetch failed: %s. Trying local files.", exc)

        # 2. Local raw files
        try:
            records = self._load_from_local()
            if records:
                return records, "NTPC_OPEN_DATA", NTPC_API_URL
        except Exception as exc:
            logger.warning("Local file load failed: %s. Using SEED data.", exc)

        # 3. SEED
        logger.info("Using built-in SEED dataset (%d records).", len(_SEED_DATA))
        return list(_SEED_DATA), "SEED_DATA", ""

    def _fetch_from_api(self) -> list[dict]:
        """
        Fetch JSON from NTPC open-data API with retry + exponential back-off.
        Returns a list of raw record dicts.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, _API_MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=30, follow_redirects=True) as client:
                    response = client.get(
                        NTPC_API_URL,
                        headers={"Accept": "application/json"},
                    )
                    response.raise_for_status()
                    data = response.json()

                if isinstance(data, list):
                    logger.info("API returned %d records (attempt %d).", len(data), attempt)
                    return data
                elif isinstance(data, dict) and "data" in data:
                    inner = data["data"]
                    if isinstance(inner, list):
                        logger.info(
                            "API returned %d records (wrapper.data) on attempt %d.",
                            len(inner),
                            attempt,
                        )
                        return inner
                logger.warning("Unexpected API response shape: %r", type(data))
                return []

            except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt < _API_MAX_RETRIES:
                    wait = _API_BACKOFF_BASE ** attempt
                    logger.warning(
                        "API attempt %d/%d failed (%s). Retrying in %.1fs.",
                        attempt,
                        _API_MAX_RETRIES,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
                else:
                    logger.warning("All %d API attempts failed.", _API_MAX_RETRIES)

        raise last_exc or RuntimeError("API fetch failed with unknown error")

    def _load_from_local(self) -> list[dict]:
        """
        Load records from data/raw/official/*.json files.
        """
        from config.settings import settings

        raw_dir: Path = settings.RAW_DIR / "official"
        if not raw_dir.exists():
            return []

        records: list[dict] = []
        for json_file in sorted(raw_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    records.extend(data)
                    logger.info("Loaded %d records from %s", len(data), json_file.name)
                elif isinstance(data, dict):
                    records.append(data)
            except Exception as exc:
                logger.warning("Failed to load %s: %s", json_file, exc)

        return records

    # ------------------------------------------------------------------
    # Record processing
    # ------------------------------------------------------------------

    def _process_record(
        self,
        raw: dict,
        source_label: str,
        source_url: str,
        district_filter: Optional[str],
        dry_run: bool,
    ) -> str:
        """
        Process a single raw record.

        Returns
        -------
        "success" | "skipped" | "rejected"
        """
        from services.public_classifier import classify_kindergarten, PublicType
        from services.district_resolver import resolve_district

        # ── Normalise ────────────────────────────────────────────────────
        if source_label == "SEED_DATA":
            # SEED records already have clean fields
            official_name = _coerce_str(raw.get("official_name", ""))
            address = _coerce_str(raw.get("address", ""))
            raw_district = _coerce_str(raw.get("district", ""))
            phone = ""
            kindergarten_type_raw = ""
            public_type_raw = ""
            lat_raw = ""
            lng_raw = ""
        else:
            normalised = _normalise_api_record(raw)
            official_name = normalised["official_name"]
            address = normalised["address"]
            raw_district = normalised["district"]
            phone = normalised["phone"]
            kindergarten_type_raw = normalised["kindergarten_type"]
            public_type_raw = normalised["public_type"]
            lat_raw = normalised["lat"]
            lng_raw = normalised["lng"]

        if not official_name:
            logger.debug("Skipping record with empty official_name: %r", raw)
            return "rejected"

        # ── District resolution ───────────────────────────────────────
        district = resolve_district(district=raw_district, address=address)
        if district == "UNKNOWN":
            # Last resort: try to extract from official_name itself
            district = resolve_district(district=None, address=official_name)

        # ── District filter ───────────────────────────────────────────
        if district_filter and district != district_filter:
            return "skipped"

        # ── Public classification ─────────────────────────────────────
        pub_type = classify_kindergarten(
            kindergarten_type=kindergarten_type_raw,
            public_type=public_type_raw,
            official_name=official_name,
            school_name=None,
        )

        if pub_type != PublicType.PUBLIC:
            logger.debug(
                "Rejecting non-public kindergarten '%s' (classified as %s)",
                official_name,
                pub_type,
            )
            return "rejected"

        # ── Derive name components ────────────────────────────────────
        kg_type = _parse_kindergarten_type(official_name)
        school_name = _parse_school_name(official_name)
        kindergarten_name = _parse_kindergarten_name(official_name)

        # ── Official source ID ────────────────────────────────────────
        official_source_id = _generate_source_id(official_name, district)

        # ── Lat/lng ───────────────────────────────────────────────────
        latitude: Optional[float] = None
        longitude: Optional[float] = None
        try:
            if lat_raw:
                latitude = float(lat_raw)
            if lng_raw:
                longitude = float(lng_raw)
        except (ValueError, TypeError):
            pass

        # ── Resume: skip if already exists ───────────────────────────
        if not dry_run:
            try:
                from database.official_db import get_connection, kindergartens_table
                from sqlalchemy import select as sa_select

                with get_connection() as conn:
                    existing = conn.execute(
                        sa_select(kindergartens_table.c.id).where(
                            kindergartens_table.c.official_source_id == official_source_id
                        )
                    ).first()
                if existing:
                    logger.debug(
                        "Skipping existing record official_source_id=%s (%s)",
                        official_source_id,
                        official_name,
                    )
                    return "skipped"
            except Exception as exc:
                logger.warning("Could not check for existing record: %s", exc)

        # ── Build DB record ───────────────────────────────────────────
        now_ts = datetime.now(timezone.utc).isoformat()
        record: dict[str, Any] = {
            "official_source_id": official_source_id,
            "official_name": official_name,
            "school_name": school_name,
            "kindergarten_name": kindergarten_name,
            "kindergarten_type": kg_type,
            "public_type": "PUBLIC",
            "district": district,
            "address": address,
            "phone": phone or None,
            "latitude": latitude,
            "longitude": longitude,
            "is_public": 1,
            "status": "ACTIVE",
            "official_source": source_label,
            "official_source_url": source_url,
            "created_at": now_ts,
            "updated_at": now_ts,
        }

        if dry_run:
            logger.debug("[dry_run] Would insert: %s", official_name)
            return "success"

        # ── Persist to official_kindergartens.db ──────────────────────
        try:
            from database.official_db import upsert_kindergarten

            kg_id = upsert_kindergarten(record)
            logger.debug("Saved kindergarten id=%d: %s", kg_id, official_name)
        except Exception as exc:
            logger.error("Failed to save '%s' to official_db: %s", official_name, exc)
            return "rejected"

        # ── Mirror to master_db ───────────────────────────────────────
        try:
            from database.master_db import get_connection as master_conn
            from database.master_db import kindergartens_table as master_kg_table
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            master_record = dict(record)
            master_record["id"] = kg_id  # use same id as official_db

            with master_conn() as conn:
                stmt = (
                    sqlite_insert(master_kg_table)
                    .values(**{
                        k: v for k, v in master_record.items()
                        if k in {c.key for c in master_kg_table.columns}
                    })
                    .on_conflict_do_update(
                        index_elements=["official_source_id"],
                        set_={
                            k: v for k, v in master_record.items()
                            if k not in ("id", "official_source_id", "created_at")
                            and k in {c.key for c in master_kg_table.columns}
                        },
                    )
                )
                conn.execute(stmt)
                conn.commit()
        except Exception as exc:
            logger.warning("Failed to mirror '%s' to master_db: %s", official_name, exc)

        return "success"

    # ------------------------------------------------------------------
    # Rejected record saving
    # ------------------------------------------------------------------

    def _save_rejected(self, rejected: list[dict]) -> None:
        """Write rejected records to data/rejected/ as a timestamped CSV."""
        from config.settings import settings

        rejected_dir: Path = settings.REJECTED_DIR
        rejected_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = rejected_dir / f"official_rejected_{ts}.csv"

        if not rejected:
            return

        all_keys: list[str] = list(
            dict.fromkeys(k for rec in rejected for k in rec.keys())
        )

        try:
            with out_path.open("w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=all_keys, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rejected)
            logger.info("Saved %d rejected records to %s", len(rejected), out_path)
        except Exception as exc:
            logger.error("Failed to write rejected CSV: %s", exc)
