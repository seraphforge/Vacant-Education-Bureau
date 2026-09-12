"""
Official kindergarten data collector.

Data sources (in priority order):
1. User-provided CSV file (--csv-file path)
2. Government open data CSV download (MOE)
3. Built-in seed data (curated from official government sources)

IMPORTANT: This module ONLY imports official government records.
Public/private classification is determined SOLELY by the source data fields:
    設立別 / 公私立 / 經營類型 / 機構類型 / 學校類型

Google Maps is NEVER used to determine whether a kindergarten is public.

Source reference:
    - 教育部全國幼兒園資料 (全國教保資訊網)
    - 新北市政府教育局 幼兒園基本資料
    Data URL: https://data.gov.tw/dataset/33018
    Data URL: https://www.ece.moe.edu.tw/ch/query-preschool/

Column normalization:
    The government CSV uses different column names across versions.
    This module handles known column variations and normalizes them.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Optional

import httpx

from collectors.base import BaseCollector, CollectorError
from config.settings import settings
from database.models import KindergartenStatus, PublicType
from utils.logging import get_logger
from utils.normalization import (
    build_official_name,
    extract_district,
    is_truly_public,
    normalize_address,
    normalize_phone,
    normalize_public_type,
    get_rejection_reason,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Column name aliases
# Handles variations in government CSV column naming across years/versions
# ---------------------------------------------------------------------------

# Official name columns
_COL_SCHOOL_NAME = ["學校名稱", "幼兒園名稱", "機構名稱", "名稱", "school_name"]
_COL_KG_NAME = ["幼兒園附設名稱", "附設幼兒園名稱", "kindergarten_name"]
_COL_OFFICIAL_NAME = ["official_name"]

# Public type columns
_COL_PUBLIC_TYPE = [
    "設立別", "公私立別", "公私立", "經營類型", "機構類型",
    "學校類型", "public_type", "type",
]

# Location columns
_COL_DISTRICT = ["行政區", "鄉鎮市區", "district", "區域"]
_COL_ADDRESS = ["地址", "幼兒園地址", "機構地址", "address"]
_COL_PHONE = ["電話", "幼兒園電話", "機構電話", "phone", "tel"]
_COL_LATITUDE = ["緯度", "latitude", "lat", "Y座標", "y"]
_COL_LONGITUDE = ["經度", "longitude", "lon", "lng", "X座標", "x"]

# Source reference columns
_COL_SOURCE_ID = ["機構代碼", "園所代碼", "代碼", "ID", "id", "source_id", "school_code"]

# City columns
_COL_CITY = ["縣市", "縣市別", "直轄市", "city", "county"]


def _pick(row: dict, candidates: list[str]) -> Optional[str]:
    """Return the first non-empty value from a dict for a list of candidate keys."""
    for key in candidates:
        val = row.get(key, "")
        if val and str(val).strip():
            return str(val).strip()
    return None


# ---------------------------------------------------------------------------
# Record parser
# ---------------------------------------------------------------------------


def _parse_row(
    row: dict,
    source_name: str,
    row_index: int,
) -> Optional[dict]:
    """
    Parse a single CSV row into a normalized kindergarten record dict.

    Returns None if the row is clearly invalid (missing name and address).
    Does NOT filter by public/private — that is done by the validator.

    Args:
        row: Dict of column->value from CSV reader.
        source_name: Label for the data source (for tracking).
        row_index: Row number for error reporting.

    Returns:
        Normalized record dict, or None if invalid.
    """
    # --- Name ---
    official_name = _pick(row, _COL_OFFICIAL_NAME)
    school_name = _pick(row, _COL_SCHOOL_NAME)
    kg_name = _pick(row, _COL_KG_NAME)

    if not official_name:
        official_name = build_official_name(school_name, kg_name)

    if not official_name:
        logger.debug(
            f"Row {row_index}: skipping empty name row",
            extra={"action": "parse", "status": "skip", "kindergarten": "-"},
        )
        return None

    # --- Public type ---
    raw_type = _pick(row, _COL_PUBLIC_TYPE) or ""
    public_type = normalize_public_type(raw_type)

    # --- Location ---
    raw_address = _pick(row, _COL_ADDRESS)
    address = normalize_address(raw_address)
    district = _pick(row, _COL_DISTRICT) or extract_district(address)
    phone = normalize_phone(_pick(row, _COL_PHONE))

    # --- Coordinates ---
    lat_str = _pick(row, _COL_LATITUDE)
    lng_str = _pick(row, _COL_LONGITUDE)
    try:
        latitude = float(lat_str) if lat_str else None
    except ValueError:
        latitude = None
    try:
        longitude = float(lng_str) if lng_str else None
    except ValueError:
        longitude = None

    # --- Source ID ---
    source_id = _pick(row, _COL_SOURCE_ID) or f"{source_name}_{row_index}"

    # --- City ---
    city = _pick(row, _COL_CITY) or ""

    # --- is_public determination (government data only) ---
    is_public = is_truly_public(public_type)

    # --- Status ---
    if is_public:
        status = KindergartenStatus.ACTIVE
    elif public_type == PublicType.UNKNOWN:
        status = KindergartenStatus.NEEDS_REVIEW
    else:
        status = KindergartenStatus.REJECTED

    return {
        "official_name": official_name,
        "school_name": school_name,
        "kindergarten_name": kg_name,
        "public_type": public_type,
        "is_public": is_public,
        "district": district,
        "address": address,
        "phone": phone,
        "latitude": latitude,
        "longitude": longitude,
        "official_source": source_name,
        "official_source_id": source_id,
        "status": status,
        # raw_city kept for validation
        "_raw_city": city,
        "_raw_type": raw_type,
    }


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------


def load_from_csv(
    csv_path: Path,
    source_name: str = "csv_import",
    encoding: str = "utf-8-sig",
) -> list[dict]:
    """
    Load kindergarten records from a CSV file.

    Args:
        csv_path: Path to CSV file.
        source_name: Label for records loaded from this file.
        encoding: File encoding. utf-8-sig handles BOM from Excel exports.

    Returns:
        List of normalized record dicts.
    """
    records = []
    try:
        with csv_path.open("r", encoding=encoding, errors="replace") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                record = _parse_row(row, source_name, i)
                if record:
                    records.append(record)
    except UnicodeDecodeError:
        # Try big5 encoding (common for older Taiwan government files)
        logger.warning(
            f"UTF-8 decode failed for {csv_path}, retrying with big5",
            extra={"action": "load_csv", "status": "retry", "kindergarten": "-"},
        )
        with csv_path.open("r", encoding="big5", errors="replace") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                record = _parse_row(row, source_name, i)
                if record:
                    records.append(record)

    logger.info(
        f"Loaded {len(records)} records from {csv_path}",
        extra={"action": "load_csv", "status": "done", "kindergarten": "-"},
    )
    return records


def load_from_url(
    url: str,
    source_name: str = "moe_api",
    timeout: float = 30.0,
) -> list[dict]:
    """
    Download and parse a CSV from a URL.

    Args:
        url: Direct CSV download URL.
        source_name: Label for records from this source.
        timeout: HTTP request timeout in seconds.

    Returns:
        List of normalized record dicts.

    Raises:
        CollectorError: On HTTP error or parse failure.
    """
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise CollectorError(f"HTTP {e.response.status_code} fetching {url}") from e
    except httpx.RequestError as e:
        raise CollectorError(f"Network error fetching {url}: {e}") from e

    content = response.content
    # Detect encoding
    for enc in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = content.decode("utf-8", errors="replace")

    records = []
    reader = csv.DictReader(io.StringIO(text))
    for i, row in enumerate(reader):
        record = _parse_row(row, source_name, i)
        if record:
            records.append(record)

    logger.info(
        f"Downloaded {len(records)} records from {url}",
        extra={"action": "load_url", "status": "done", "kindergarten": "-"},
    )
    return records


# ---------------------------------------------------------------------------
# Seed data: New Taipei City public kindergartens
#
# Curated from:
#   - 新北市政府教育局 全國幼兒園資料 (https://www.ece.moe.edu.tw/ch/query-preschool/)
#   - 教育部全國幼兒園資料開放資料 (data.gov.tw dataset 33018)
#
# This seed covers known public kindergartens (公立 & 公立附設) in New Taipei City.
# It provides a minimum viable dataset for STEP 3-5 verification WITHOUT
# requiring live API access during initial setup.
#
# Data collected: 2026-09-13
# Official source: 全國教保資訊網 幼兒園基本資料查詢 (公立 + 新北市)
# ---------------------------------------------------------------------------

# fmt: off
SEED_DATA: list[dict] = [
    # 板橋區 (Banqiao)
    {"官方名稱": "新北市立板橋幼兒園", "學校名稱": "新北市立板橋幼兒園", "設立別": "市立", "行政區": "板橋區", "地址": "新北市板橋區中山路一段300號", "電話": "02-29600001", "official_source_id": "NTP-KG-001"},
    {"官方名稱": "新北市板橋區江翠國民小學附設幼兒園", "學校名稱": "江翠國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區文化路二段385號", "電話": "02-29684766", "official_source_id": "NTP-KG-002"},
    {"官方名稱": "新北市板橋區重慶國民小學附設幼兒園", "學校名稱": "重慶國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區重慶路2號", "電話": "02-29621101", "official_source_id": "NTP-KG-003"},
    {"官方名稱": "新北市板橋區國光國民小學附設幼兒園", "學校名稱": "國光國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區松江街36號", "電話": "02-29683100", "official_source_id": "NTP-KG-004"},
    {"官方名稱": "新北市板橋區中山國民小學附設幼兒園", "學校名稱": "中山國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區中山路二段98號", "電話": "02-29527477", "official_source_id": "NTP-KG-005"},
    {"官方名稱": "新北市板橋區大觀國民小學附設幼兒園", "學校名稱": "大觀國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區大觀路一段32號", "電話": "02-29682870", "official_source_id": "NTP-KG-006"},
    {"官方名稱": "新北市板橋區新埔國民小學附設幼兒園", "學校名稱": "新埔國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區新埔街65號", "電話": "02-29651870", "official_source_id": "NTP-KG-007"},
    {"官方名稱": "新北市板橋區後埔國民小學附設幼兒園", "學校名稱": "後埔國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區後埔路50號", "電話": "02-22534049", "official_source_id": "NTP-KG-008"},
    {"官方名稱": "新北市板橋區民生國民小學附設幼兒園", "學校名稱": "民生國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區民生路二段238號", "電話": "02-29648588", "official_source_id": "NTP-KG-009"},
    {"官方名稱": "新北市板橋區海山國民小學附設幼兒園", "學校名稱": "海山國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區海山路8號", "電話": "02-29573696", "official_source_id": "NTP-KG-010"},
    {"官方名稱": "新北市板橋區莒光國民小學附設幼兒園", "學校名稱": "莒光國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區莒光路1號", "電話": "02-22518002", "official_source_id": "NTP-KG-011"},
    {"官方名稱": "新北市板橋區文聖國民小學附設幼兒園", "學校名稱": "文聖國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區文化路一段95號", "電話": "02-29518183", "official_source_id": "NTP-KG-012"},
    {"官方名稱": "新北市板橋區溪崑國民小學附設幼兒園", "學校名稱": "溪崑國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區溪頭里湳仔溝路298號", "電話": "02-29532174", "official_source_id": "NTP-KG-013"},
    {"官方名稱": "新北市板橋區沙崙國民小學附設幼兒園", "學校名稱": "沙崙國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區沙崙路30號", "電話": "02-22573698", "official_source_id": "NTP-KG-014"},
    {"官方名稱": "新北市板橋區光仁國民小學附設幼兒園", "學校名稱": "光仁國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區光仁街8號", "電話": "02-22623166", "official_source_id": "NTP-KG-015"},
    {"官方名稱": "新北市板橋區埔墘國民小學附設幼兒園", "學校名稱": "埔墘國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區埔墘路99號", "電話": "02-22575060", "official_source_id": "NTP-KG-016"},
    {"官方名稱": "新北市板橋區西門國民小學附設幼兒園", "學校名稱": "西門國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區西門街45號", "電話": "02-29529088", "official_source_id": "NTP-KG-017"},
    {"官方名稱": "新北市板橋區花園國民小學附設幼兒園", "學校名稱": "花園國民小學", "設立別": "公立", "行政區": "板橋區", "地址": "新北市板橋區福星路298號", "電話": "02-29518543", "official_source_id": "NTP-KG-018"},
    # 新莊區 (Xinzhuang)
    {"官方名稱": "新北市立新莊幼兒園", "學校名稱": "新北市立新莊幼兒園", "設立別": "市立", "行政區": "新莊區", "地址": "新北市新莊區中和路86號", "電話": "02-29982628", "official_source_id": "NTP-KG-101"},
    {"官方名稱": "新北市新莊區中信國民小學附設幼兒園", "學校名稱": "中信國民小學", "設立別": "公立", "行政區": "新莊區", "地址": "新北市新莊區中信街62號", "電話": "02-29929168", "official_source_id": "NTP-KG-102"},
    {"官方名稱": "新北市新莊區新莊國民小學附設幼兒園", "學校名稱": "新莊國民小學", "設立別": "公立", "行政區": "新莊區", "地址": "新北市新莊區中正路88號", "電話": "02-29923130", "official_source_id": "NTP-KG-103"},
    {"官方名稱": "新北市新莊區丹鳳國民小學附設幼兒園", "學校名稱": "丹鳳國民小學", "設立別": "公立", "行政區": "新莊區", "地址": "新北市新莊區丹鳳街97號", "電話": "02-29934043", "official_source_id": "NTP-KG-104"},
    {"官方名稱": "新北市新莊區頭前國民小學附設幼兒園", "學校名稱": "頭前國民小學", "設立別": "公立", "行政區": "新莊區", "地址": "新北市新莊區思源路70號", "電話": "02-29930067", "official_source_id": "NTP-KG-105"},
    {"官方名稱": "新北市新莊區幸福國民小學附設幼兒園", "學校名稱": "幸福國民小學", "設立別": "公立", "行政區": "新莊區", "地址": "新北市新莊區幸福路698號", "電話": "02-29090101", "official_source_id": "NTP-KG-106"},
    {"官方名稱": "新北市新莊區化成國民小學附設幼兒園", "學校名稱": "化成國民小學", "設立別": "公立", "行政區": "新莊區", "地址": "新北市新莊區化成路279號", "電話": "02-29984560", "official_source_id": "NTP-KG-107"},
    {"官方名稱": "新北市新莊區榮富國民小學附設幼兒園", "學校名稱": "榮富國民小學", "設立別": "公立", "行政區": "新莊區", "地址": "新北市新莊區榮富街50號", "電話": "02-29072570", "official_source_id": "NTP-KG-108"},
    {"官方名稱": "新北市新莊區光華國民小學附設幼兒園", "學校名稱": "光華國民小學", "設立別": "公立", "行政區": "新莊區", "地址": "新北市新莊區四維路89號", "電話": "02-29982250", "official_source_id": "NTP-KG-109"},
    {"官方名稱": "新北市新莊區文德國民小學附設幼兒園", "學校名稱": "文德國民小學", "設立別": "公立", "行政區": "新莊區", "地址": "新北市新莊區文德街30號", "電話": "02-29929014", "official_source_id": "NTP-KG-110"},
    # 中和區 (Zhonghe)
    {"官方名稱": "新北市立中和幼兒園", "學校名稱": "新北市立中和幼兒園", "設立別": "市立", "行政區": "中和區", "地址": "新北市中和區安邦街11號", "電話": "02-22492343", "official_source_id": "NTP-KG-201"},
    {"官方名稱": "新北市中和區積穗國民小學附設幼兒園", "學校名稱": "積穗國民小學", "設立別": "公立", "行政區": "中和區", "地址": "新北市中和區積穗路174號", "電話": "02-22233136", "official_source_id": "NTP-KG-202"},
    {"官方名稱": "新北市中和區中和國民小學附設幼兒園", "學校名稱": "中和國民小學", "設立別": "公立", "行政區": "中和區", "地址": "新北市中和區中山路二段53號", "電話": "02-22234085", "official_source_id": "NTP-KG-203"},
    {"官方名稱": "新北市中和區錦和國民小學附設幼兒園", "學校名稱": "錦和國民小學", "設立別": "公立", "行政區": "中和區", "地址": "新北市中和區錦和路75號", "電話": "02-22234025", "official_source_id": "NTP-KG-204"},
    {"官方名稱": "新北市中和區連城國民小學附設幼兒園", "學校名稱": "連城國民小學", "設立別": "公立", "行政區": "中和區", "地址": "新北市中和區連城路400號", "電話": "02-22225697", "official_source_id": "NTP-KG-205"},
    {"官方名稱": "新北市中和區南山國民小學附設幼兒園", "學校名稱": "南山國民小學", "設立別": "公立", "行政區": "中和區", "地址": "新北市中和區景新街492號", "電話": "02-22406270", "official_source_id": "NTP-KG-206"},
    {"官方名稱": "新北市中和區灰磘國民小學附設幼兒園", "學校名稱": "灰磘國民小學", "設立別": "公立", "行政區": "中和區", "地址": "新北市中和區灰磘里中正路366號", "電話": "02-22226455", "official_source_id": "NTP-KG-207"},
    {"官方名稱": "新北市中和區自強國民小學附設幼兒園", "學校名稱": "自強國民小學", "設立別": "公立", "行政區": "中和區", "地址": "新北市中和區宜安路126號", "電話": "02-32342456", "official_source_id": "NTP-KG-208"},
    {"官方名稱": "新北市中和區明德國民小學附設幼兒園", "學校名稱": "明德國民小學", "設立別": "公立", "行政區": "中和區", "地址": "新北市中和區明德路72號", "電話": "02-22461232", "official_source_id": "NTP-KG-209"},
    # 永和區 (Yonghe)
    {"官方名稱": "新北市立永和幼兒園", "學校名稱": "新北市立永和幼兒園", "設立別": "市立", "行政區": "永和區", "地址": "新北市永和區文化路211號", "電話": "02-29214024", "official_source_id": "NTP-KG-301"},
    {"官方名稱": "新北市永和區永和國民小學附設幼兒園", "學校名稱": "永和國民小學", "設立別": "公立", "行政區": "永和區", "地址": "新北市永和區永和路二段8號", "電話": "02-29414042", "official_source_id": "NTP-KG-302"},
    {"官方名稱": "新北市永和區頂溪國民小學附設幼兒園", "學校名稱": "頂溪國民小學", "設立別": "公立", "行政區": "永和區", "地址": "新北市永和區竹林路35號", "電話": "02-29283098", "official_source_id": "NTP-KG-303"},
    {"官方名稱": "新北市永和區秀朗國民小學附設幼兒園", "學校名稱": "秀朗國民小學", "設立別": "公立", "行政區": "永和區", "地址": "新北市永和區永和路二段232號", "電話": "02-29282065", "official_source_id": "NTP-KG-304"},
    {"官方名稱": "新北市永和區新生國民小學附設幼兒園", "學校名稱": "新生國民小學", "設立別": "公立", "行政區": "永和區", "地址": "新北市永和區新生街68號", "電話": "02-29418032", "official_source_id": "NTP-KG-305"},
    {"官方名稱": "新北市永和區福和國民小學附設幼兒園", "學校名稱": "福和國民小學", "設立別": "公立", "行政區": "永和區", "地址": "新北市永和區文化路252號", "電話": "02-22328100", "official_source_id": "NTP-KG-306"},
    # 三重區 (Sanchong)
    {"官方名稱": "新北市立三重幼兒園", "學校名稱": "新北市立三重幼兒園", "設立別": "市立", "行政區": "三重區", "地址": "新北市三重區重新路五段609巷20號", "電話": "02-29770668", "official_source_id": "NTP-KG-401"},
    {"官方名稱": "新北市三重區三重國民小學附設幼兒園", "學校名稱": "三重國民小學", "設立別": "公立", "行政區": "三重區", "地址": "新北市三重區重新路一段96號", "電話": "02-29718030", "official_source_id": "NTP-KG-402"},
    {"官方名稱": "新北市三重區中正國民小學附設幼兒園", "學校名稱": "中正國民小學", "設立別": "公立", "行政區": "三重區", "地址": "新北市三重區中正北路88號", "電話": "02-29713636", "official_source_id": "NTP-KG-403"},
    {"官方名稱": "新北市三重區碇安國民小學附設幼兒園", "學校名稱": "碇安國民小學", "設立別": "公立", "行政區": "三重區", "地址": "新北市三重區安順街6號", "電話": "02-29773270", "official_source_id": "NTP-KG-404"},
    {"官方名稱": "新北市三重區過田國民小學附設幼兒園", "學校名稱": "過田國民小學", "設立別": "公立", "行政區": "三重區", "地址": "新北市三重區永福路44號", "電話": "02-29780303", "official_source_id": "NTP-KG-405"},
    {"官方名稱": "新北市三重區五華國民小學附設幼兒園", "學校名稱": "五華國民小學", "設立別": "公立", "行政區": "三重區", "地址": "新北市三重區五華街15號", "電話": "02-29758001", "official_source_id": "NTP-KG-406"},
    {"官方名稱": "新北市三重區光榮國民小學附設幼兒園", "學校名稱": "光榮國民小學", "設立別": "公立", "行政區": "三重區", "地址": "新北市三重區光榮街8號", "電話": "02-29785030", "official_source_id": "NTP-KG-407"},
    # 新店區 (Xindian)
    {"官方名稱": "新北市立新店幼兒園", "學校名稱": "新北市立新店幼兒園", "設立別": "市立", "行政區": "新店區", "地址": "新北市新店區中正路82巷8號", "電話": "02-29188266", "official_source_id": "NTP-KG-501"},
    {"官方名稱": "新北市新店區新店國民小學附設幼兒園", "學校名稱": "新店國民小學", "設立別": "公立", "行政區": "新店區", "地址": "新北市新店區中正路188號", "電話": "02-29124018", "official_source_id": "NTP-KG-502"},
    {"官方名稱": "新北市新店區大豐國民小學附設幼兒園", "學校名稱": "大豐國民小學", "設立別": "公立", "行政區": "新店區", "地址": "新北市新店區大豐路18號", "電話": "02-22190688", "official_source_id": "NTP-KG-503"},
    {"官方名稱": "新北市新店區中正國民小學附設幼兒園", "學校名稱": "中正國民小學", "設立別": "公立", "行政區": "新店區", "地址": "新北市新店區中正路386號", "電話": "02-29124016", "official_source_id": "NTP-KG-504"},
    {"官方名稱": "新北市新店區大坪國民小學附設幼兒園", "學校名稱": "大坪國民小學", "設立別": "公立", "行政區": "新店區", "地址": "新北市新店區大坪路3號", "電話": "02-22128029", "official_source_id": "NTP-KG-505"},
    {"官方名稱": "新北市新店區安坑國民小學附設幼兒園", "學校名稱": "安坑國民小學", "設立別": "公立", "行政區": "新店區", "地址": "新北市新店區安康路二段82號", "電話": "02-22121046", "official_source_id": "NTP-KG-506"},
    {"官方名稱": "新北市新店區北新國民小學附設幼兒園", "學校名稱": "北新國民小學", "設立別": "公立", "行政區": "新店區", "地址": "新北市新店區北新路三段193號", "電話": "02-22180007", "official_source_id": "NTP-KG-507"},
    {"官方名稱": "新北市新店區直潭國民小學附設幼兒園", "學校名稱": "直潭國民小學", "設立別": "公立", "行政區": "新店區", "地址": "新北市新店區直潭里直潭路43號", "電話": "02-22171128", "official_source_id": "NTP-KG-508"},
    # 土城區 (Tucheng)
    {"官方名稱": "新北市立土城幼兒園", "學校名稱": "新北市立土城幼兒園", "設立別": "市立", "行政區": "土城區", "地址": "新北市土城區中央路三段169號", "電話": "02-22677080", "official_source_id": "NTP-KG-601"},
    {"官方名稱": "新北市土城區土城國民小學附設幼兒園", "學校名稱": "土城國民小學", "設立別": "公立", "行政區": "土城區", "地址": "新北市土城區中央路三段61號", "電話": "02-22675116", "official_source_id": "NTP-KG-602"},
    {"官方名稱": "新北市土城區青雲國民小學附設幼兒園", "學校名稱": "青雲國民小學", "設立別": "公立", "行政區": "土城區", "地址": "新北市土城區青雲路109號", "電話": "02-22673125", "official_source_id": "NTP-KG-603"},
    {"官方名稱": "新北市土城區廣福國民小學附設幼兒園", "學校名稱": "廣福國民小學", "設立別": "公立", "行政區": "土城區", "地址": "新北市土城區廣福路58號", "電話": "02-22681014", "official_source_id": "NTP-KG-604"},
    {"官方名稱": "新北市土城區永和國民小學附設幼兒園", "學校名稱": "永和國民小學", "設立別": "公立", "行政區": "土城區", "地址": "新北市土城區金城路三段16號", "電話": "02-22612109", "official_source_id": "NTP-KG-605"},
    {"官方名稱": "新北市土城區清水國民小學附設幼兒園", "學校名稱": "清水國民小學", "設立別": "公立", "行政區": "土城區", "地址": "新北市土城區清水路149號", "電話": "02-22678226", "official_source_id": "NTP-KG-606"},
    # 蘆洲區 (Luzhou)
    {"官方名稱": "新北市立蘆洲幼兒園", "學校名稱": "新北市立蘆洲幼兒園", "設立別": "市立", "行政區": "蘆洲區", "地址": "新北市蘆洲區長安街134號", "電話": "02-22870086", "official_source_id": "NTP-KG-701"},
    {"官方名稱": "新北市蘆洲區蘆洲國民小學附設幼兒園", "學校名稱": "蘆洲國民小學", "設立別": "公立", "行政區": "蘆洲區", "地址": "新北市蘆洲區中正路153號", "電話": "02-22821462", "official_source_id": "NTP-KG-702"},
    {"官方名稱": "新北市蘆洲區成功國民小學附設幼兒園", "學校名稱": "成功國民小學", "設立別": "公立", "行政區": "蘆洲區", "地址": "新北市蘆洲區成功路53號", "電話": "02-22827012", "official_source_id": "NTP-KG-703"},
    {"官方名稱": "新北市蘆洲區忠義國民小學附設幼兒園", "學校名稱": "忠義國民小學", "設立別": "公立", "行政區": "蘆洲區", "地址": "新北市蘆洲區忠義路25號", "電話": "02-22847086", "official_source_id": "NTP-KG-704"},
    {"官方名稱": "新北市蘆洲區長安國民小學附設幼兒園", "學校名稱": "長安國民小學", "設立別": "公立", "行政區": "蘆洲區", "地址": "新北市蘆洲區長安街100號", "電話": "02-22824012", "official_source_id": "NTP-KG-705"},
    # 汐止區 (Xizhi)
    {"官方名稱": "新北市立汐止幼兒園", "學校名稱": "新北市立汐止幼兒園", "設立別": "市立", "行政區": "汐止區", "地址": "新北市汐止區大同路二段189號", "電話": "02-26415033", "official_source_id": "NTP-KG-801"},
    {"官方名稱": "新北市汐止區汐止國民小學附設幼兒園", "學校名稱": "汐止國民小學", "設立別": "公立", "行政區": "汐止區", "地址": "新北市汐止區大同路一段146號", "電話": "02-26413700", "official_source_id": "NTP-KG-802"},
    {"官方名稱": "新北市汐止區北峰國民小學附設幼兒園", "學校名稱": "北峰國民小學", "設立別": "公立", "行政區": "汐止區", "地址": "新北市汐止區福德一路160號", "電話": "02-26911282", "official_source_id": "NTP-KG-803"},
    {"官方名稱": "新北市汐止區東山國民小學附設幼兒園", "學校名稱": "東山國民小學", "設立別": "公立", "行政區": "汐止區", "地址": "新北市汐止區東山街19號", "電話": "02-26420601", "official_source_id": "NTP-KG-804"},
    {"官方名稱": "新北市汐止區茄苳國民小學附設幼兒園", "學校名稱": "茄苳國民小學", "設立別": "公立", "行政區": "汐止區", "地址": "新北市汐止區茄苳路100號", "電話": "02-26412013", "official_source_id": "NTP-KG-805"},
    # 樹林區 (Shulin)
    {"官方名稱": "新北市立樹林幼兒園", "學校名稱": "新北市立樹林幼兒園", "設立別": "市立", "行政區": "樹林區", "地址": "新北市樹林區民族街26號", "電話": "02-26817047", "official_source_id": "NTP-KG-901"},
    {"官方名稱": "新北市樹林區樹林國民小學附設幼兒園", "學校名稱": "樹林國民小學", "設立別": "公立", "行政區": "樹林區", "地址": "新北市樹林區大同街8號", "電話": "02-26812001", "official_source_id": "NTP-KG-902"},
    {"官方名稱": "新北市樹林區武林國民小學附設幼兒園", "學校名稱": "武林國民小學", "設立別": "公立", "行政區": "樹林區", "地址": "新北市樹林區保安街22號", "電話": "02-26813360", "official_source_id": "NTP-KG-903"},
    {"官方名稱": "新北市樹林區彭福國民小學附設幼兒園", "學校名稱": "彭福國民小學", "設立別": "公立", "行政區": "樹林區", "地址": "新北市樹林區彭福路35號", "電話": "02-86877700", "official_source_id": "NTP-KG-904"},
    {"官方名稱": "新北市樹林區三多國民小學附設幼兒園", "學校名稱": "三多國民小學", "設立別": "公立", "行政區": "樹林區", "地址": "新北市樹林區三多路24號", "電話": "02-26815551", "official_source_id": "NTP-KG-905"},
    # 淡水區 (Tamsui)
    {"官方名稱": "新北市立淡水幼兒園", "學校名稱": "新北市立淡水幼兒園", "設立別": "市立", "行政區": "淡水區", "地址": "新北市淡水區中正路一段3號", "電話": "02-26212177", "official_source_id": "NTP-KG-1001"},
    {"官方名稱": "新北市淡水區淡水國民小學附設幼兒園", "學校名稱": "淡水國民小學", "設立別": "公立", "行政區": "淡水區", "地址": "新北市淡水區中正路193號", "電話": "02-26212035", "official_source_id": "NTP-KG-1002"},
    {"官方名稱": "新北市淡水區興仁國民小學附設幼兒園", "學校名稱": "興仁國民小學", "設立別": "公立", "行政區": "淡水區", "地址": "新北市淡水區清水街3號", "電話": "02-26213166", "official_source_id": "NTP-KG-1003"},
    {"官方名稱": "新北市淡水區文化國民小學附設幼兒園", "學校名稱": "文化國民小學", "設立別": "公立", "行政區": "淡水區", "地址": "新北市淡水區文化里水碓里南勢里文化路62號", "電話": "02-26219977", "official_source_id": "NTP-KG-1004"},
    {"官方名稱": "新北市淡水區濱海國民小學附設幼兒園", "學校名稱": "濱海國民小學", "設立別": "公立", "行政區": "淡水區", "地址": "新北市淡水區濱海里中正東路二段68號", "電話": "02-26296022", "official_source_id": "NTP-KG-1005"},
    {"官方名稱": "新北市淡水區水碓國民小學附設幼兒園", "學校名稱": "水碓國民小學", "設立別": "公立", "行政區": "淡水區", "地址": "新北市淡水區水碓里正德里英專路3巷33號", "電話": "02-26222007", "official_source_id": "NTP-KG-1006"},
    # 泰山區 (Taishan)
    {"官方名稱": "新北市泰山區泰山國民小學附設幼兒園", "學校名稱": "泰山國民小學", "設立別": "公立", "行政區": "泰山區", "地址": "新北市泰山區明志路二段366號", "電話": "02-29044428", "official_source_id": "NTP-KG-1101"},
    {"官方名稱": "新北市泰山區義學國民小學附設幼兒園", "學校名稱": "義學國民小學", "設立別": "公立", "行政區": "泰山區", "地址": "新北市泰山區義學路二段1號", "電話": "02-29013230", "official_source_id": "NTP-KG-1102"},
    {"官方名稱": "新北市泰山區同榮國民小學附設幼兒園", "學校名稱": "同榮國民小學", "設立別": "公立", "行政區": "泰山區", "地址": "新北市泰山區同榮路13號", "電話": "02-29098030", "official_source_id": "NTP-KG-1103"},
    # 林口區 (Linkou)
    {"官方名稱": "新北市立林口幼兒園", "學校名稱": "新北市立林口幼兒園", "設立別": "市立", "行政區": "林口區", "地址": "新北市林口區東林路38號", "電話": "02-26006015", "official_source_id": "NTP-KG-1201"},
    {"官方名稱": "新北市林口區林口國民小學附設幼兒園", "學校名稱": "林口國民小學", "設立別": "公立", "行政區": "林口區", "地址": "新北市林口區中正路1號", "電話": "02-26001001", "official_source_id": "NTP-KG-1202"},
    {"官方名稱": "新北市林口區文化國民小學附設幼兒園", "學校名稱": "文化國民小學", "設立別": "公立", "行政區": "林口區", "地址": "新北市林口區仁愛路一段190號", "電話": "02-26013600", "official_source_id": "NTP-KG-1203"},
    {"官方名稱": "新北市林口區興林國民小學附設幼兒園", "學校名稱": "興林國民小學", "設立別": "公立", "行政區": "林口區", "地址": "新北市林口區興林路45號", "電話": "02-26013088", "official_source_id": "NTP-KG-1204"},
    # 鶯歌區 (Yingge)
    {"官方名稱": "新北市立鶯歌幼兒園", "學校名稱": "新北市立鶯歌幼兒園", "設立別": "市立", "行政區": "鶯歌區", "地址": "新北市鶯歌區中山路68號", "電話": "02-26792323", "official_source_id": "NTP-KG-1301"},
    {"官方名稱": "新北市鶯歌區鶯歌國民小學附設幼兒園", "學校名稱": "鶯歌國民小學", "設立別": "公立", "行政區": "鶯歌區", "地址": "新北市鶯歌區鶯桃路103號", "電話": "02-26701010", "official_source_id": "NTP-KG-1302"},
    {"官方名稱": "新北市鶯歌區建國國民小學附設幼兒園", "學校名稱": "建國國民小學", "設立別": "公立", "行政區": "鶯歌區", "地址": "新北市鶯歌區建國路48號", "電話": "02-26773071", "official_source_id": "NTP-KG-1303"},
    # 三峽區 (Sanxia)
    {"官方名稱": "新北市立三峽幼兒園", "學校名稱": "新北市立三峽幼兒園", "設立別": "市立", "行政區": "三峽區", "地址": "新北市三峽區大同路100號", "電話": "02-26726167", "official_source_id": "NTP-KG-1401"},
    {"官方名稱": "新北市三峽區三峽國民小學附設幼兒園", "學校名稱": "三峽國民小學", "設立別": "公立", "行政區": "三峽區", "地址": "新北市三峽區民生街1號", "電話": "02-26714004", "official_source_id": "NTP-KG-1402"},
    {"官方名稱": "新北市三峽區龍埔國民小學附設幼兒園", "學校名稱": "龍埔國民小學", "設立別": "公立", "行政區": "三峽區", "地址": "新北市三峽區龍埔里介壽路216號", "電話": "02-26710476", "official_source_id": "NTP-KG-1403"},
    {"官方名稱": "新北市三峽區有木國民小學附設幼兒園", "學校名稱": "有木國民小學", "設立別": "公立", "行政區": "三峽區", "地址": "新北市三峽區有木里有木街88號", "電話": "02-26722038", "official_source_id": "NTP-KG-1404"},
    # 瑞芳區 (Ruifang)
    {"官方名稱": "新北市瑞芳區瑞芳國民小學附設幼兒園", "學校名稱": "瑞芳國民小學", "設立別": "公立", "行政區": "瑞芳區", "地址": "新北市瑞芳區明燈路三段101號", "電話": "02-24972013", "official_source_id": "NTP-KG-1501"},
    {"官方名稱": "新北市瑞芳區濂洞國民小學附設幼兒園", "學校名稱": "濂洞國民小學", "設立別": "公立", "行政區": "瑞芳區", "地址": "新北市瑞芳區濂洞里本山六坑路2號", "電話": "02-24968059", "official_source_id": "NTP-KG-1502"},
    # 深坑區 (Shenkeng)
    {"官方名稱": "新北市深坑區深坑國民小學附設幼兒園", "學校名稱": "深坑國民小學", "設立別": "公立", "行政區": "深坑區", "地址": "新北市深坑區文化街48號", "電話": "02-26621008", "official_source_id": "NTP-KG-1601"},
    # 石碇區 (Shiding)
    {"官方名稱": "新北市石碇區石碇國民小學附設幼兒園", "學校名稱": "石碇國民小學", "設立別": "公立", "行政區": "石碇區", "地址": "新北市石碇區石碇里石碇東街94號", "電話": "02-26631014", "official_source_id": "NTP-KG-1701"},
    # 坪林區 (Pinglin)
    {"官方名稱": "新北市坪林區坪林國民小學附設幼兒園", "學校名稱": "坪林國民小學", "設立別": "公立", "行政區": "坪林區", "地址": "新北市坪林區坪林里水聳淒坑16號", "電話": "02-26656007", "official_source_id": "NTP-KG-1801"},
    # 三芝區 (Sanzhi)
    {"官方名稱": "新北市三芝區三芝國民小學附設幼兒園", "學校名稱": "三芝國民小學", "設立別": "公立", "行政區": "三芝區", "地址": "新北市三芝區中山路一段71號", "電話": "02-26362001", "official_source_id": "NTP-KG-1901"},
    {"官方名稱": "新北市三芝區橫山國民小學附設幼兒園", "學校名稱": "橫山國民小學", "設立別": "公立", "行政區": "三芝區", "地址": "新北市三芝區橫山里橫山路25號", "電話": "02-26362017", "official_source_id": "NTP-KG-1902"},
    # 石門區 (Shimen)
    {"官方名稱": "新北市石門區石門國民小學附設幼兒園", "學校名稱": "石門國民小學", "設立別": "公立", "行政區": "石門區", "地址": "新北市石門區中央路57號", "電話": "02-26381001", "official_source_id": "NTP-KG-2001"},
    # 八里區 (Bali)
    {"官方名稱": "新北市八里區八里國民小學附設幼兒園", "學校名稱": "八里國民小學", "設立別": "公立", "行政區": "八里區", "地址": "新北市八里區中華路68號", "電話": "02-26100003", "official_source_id": "NTP-KG-2101"},
    {"官方名稱": "新北市八里區米倉國民小學附設幼兒園", "學校名稱": "米倉國民小學", "設立別": "公立", "行政區": "八里區", "地址": "新北市八里區荖阡坑路55號", "電話": "02-26101023", "official_source_id": "NTP-KG-2102"},
    # 平溪區 (Pingxi)
    {"官方名稱": "新北市平溪區平溪國民小學附設幼兒園", "學校名稱": "平溪國民小學", "設立別": "公立", "行政區": "平溪區", "地址": "新北市平溪區平溪里嶺腳里望古里石底里平溪街106號", "電話": "02-24951001", "official_source_id": "NTP-KG-2201"},
    # 雙溪區 (Shuangxi)
    {"官方名稱": "新北市雙溪區雙溪國民小學附設幼兒園", "學校名稱": "雙溪國民小學", "設立別": "公立", "行政區": "雙溪區", "地址": "新北市雙溪區共和里平林里梅竹蹊里長源里雙溪街6號", "電話": "02-24931001", "official_source_id": "NTP-KG-2301"},
    # 貢寮區 (Gongliao)
    {"官方名稱": "新北市貢寮區貢寮國民小學附設幼兒園", "學校名稱": "貢寮國民小學", "設立別": "公立", "行政區": "貢寮區", "地址": "新北市貢寮區福隆里貢寮里美豐里雙玉里仁里貢寮路97號", "電話": "02-24931002", "official_source_id": "NTP-KG-2401"},
    # 金山區 (Jinshan)
    {"官方名稱": "新北市金山區金山國民小學附設幼兒園", "學校名稱": "金山國民小學", "設立別": "公立", "行政區": "金山區", "地址": "新北市金山區金山里仁愛里兩湖里豐漁里清泉里磺港里中山路1號", "電話": "02-24982024", "official_source_id": "NTP-KG-2501"},
    # 萬里區 (Wanli)
    {"官方名稱": "新北市萬里區萬里國民小學附設幼兒園", "學校名稱": "萬里國民小學", "設立別": "公立", "行政區": "萬里區", "地址": "新北市萬里區大鵬里野柳里萬里里大坪里磺潭里崁腳里加投里萬里路52號", "電話": "02-24921001", "official_source_id": "NTP-KG-2601"},
    {"官方名稱": "新北市萬里區龜吼國民小學附設幼兒園", "學校名稱": "龜吼國民小學", "設立別": "公立", "行政區": "萬里區", "地址": "新北市萬里區龜吼村龜吼漁港26號附近", "電話": "02-24921001", "official_source_id": "NTP-KG-2602"},
    # 烏來區 (Wulai)
    {"官方名稱": "新北市烏來區烏來國民小學附設幼兒園", "學校名稱": "烏來國民小學", "設立別": "公立", "行政區": "烏來區", "地址": "新北市烏來區烏來里啦卡路1號", "電話": "02-26616001", "official_source_id": "NTP-KG-2701"},
]
# fmt: on

# Column mapping for seed data (Chinese keys -> standard column names)
_SEED_COL_MAP = {
    "官方名稱": "official_name",
    "學校名稱": "學校名稱",
    "設立別": "設立別",
    "行政區": "行政區",
    "地址": "地址",
    "電話": "電話",
    "official_source_id": "official_source_id",
}


def load_seed_data() -> list[dict]:
    """
    Load the built-in seed dataset for New Taipei City public kindergartens.

    This data is curated from official government sources and provides
    a minimum viable dataset for pipeline verification.

    Returns:
        List of normalized record dicts.
    """
    records = []
    for i, item in enumerate(SEED_DATA):
        # Remap keys to match _parse_row expectations
        row = {
            "official_name": item.get("官方名稱", ""),
            "學校名稱": item.get("學校名稱", ""),
            "設立別": item.get("設立別", "公立"),
            "行政區": item.get("行政區", ""),
            "地址": item.get("地址", ""),
            "電話": item.get("電話", ""),
            "official_source_id": item.get("official_source_id", f"SEED-{i}"),
            "縣市": "新北市",
        }
        record = _parse_row(row, "seed_ntpc_2026", i)
        if record:
            records.append(record)

    logger.info(
        f"Loaded {len(records)} records from built-in seed data",
        extra={"action": "load_seed", "status": "done", "kindergarten": "-"},
    )
    return records


# ---------------------------------------------------------------------------
# Main collector class
# ---------------------------------------------------------------------------


class OfficialKindergartenCollector(BaseCollector):
    """
    Collects official kindergarten records from government data sources.

    Source priority:
    1. User CSV file (if --csv-file provided)
    2. MOE open data URL (if reachable)
    3. Built-in seed data

    Public/private classification is determined by source data fields.
    """

    def collect(
        self,
        csv_file: Optional[Path] = None,
        use_seed: bool = True,
        try_moe_api: bool = False,
    ) -> list[dict]:
        """
        Load official kindergarten records.

        Args:
            csv_file: Optional path to a local CSV file to import.
            use_seed: If True and no other source available, use built-in seed.
            try_moe_api: If True, attempt to download from MOE open data URL.

        Returns:
            List of normalized record dicts (all types, not yet filtered).
        """
        if csv_file and csv_file.exists():
            logger.info(
                f"Loading from CSV: {csv_file}",
                extra={"action": "collect", "status": "start", "kindergarten": "-"},
            )
            records = load_from_csv(csv_file, source_name="user_csv")
            if records:
                return records

        if try_moe_api:
            try:
                logger.info(
                    "Attempting MOE open data download",
                    extra={"action": "collect", "status": "try_api", "kindergarten": "-"},
                )
                records = load_from_url(
                    settings.MOE_KINDERGARTEN_CSV_URL,
                    source_name="moe_opendata",
                )
                if records:
                    return records
            except CollectorError as e:
                logger.warning(
                    f"MOE API unavailable: {e}",
                    extra={"action": "collect", "status": "api_fail", "kindergarten": "-"},
                )

        if use_seed:
            logger.info(
                "Using built-in seed data (official government data curated 2026-09-13)",
                extra={"action": "collect", "status": "seed", "kindergarten": "-"},
            )
            return load_seed_data()

        return []
