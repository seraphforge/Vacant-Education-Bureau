"""
Public kindergarten validator.

Validates all kindergarten records and:
1. Accepts: New Taipei City public kindergartens (is_public=True + district is valid)
2. Rejects: private, quasi-public, nonprofit, wrong city, unknown type, etc.

Produces:
    data/validation/public_kindergartens_valid.csv
    data/validation/rejected_kindergartens.csv

Key principle:
    Government data determines public status.
    Google Maps is NEVER consulted in this validation step.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import settings
from database.models import KindergartenStatus, PublicType
from utils.logging import get_logger
from utils.normalization import (
    extract_district,
    get_rejection_reason,
    is_truly_public,
    normalize_public_type,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Result of validating a single kindergarten record."""
    is_valid: bool
    rejection_reason: Optional[str] = None
    notes: str = ""


@dataclass
class ValidationReport:
    """Aggregated validation statistics."""
    total_input: int = 0
    total_valid: int = 0
    total_rejected: int = 0

    # Rejection breakdown
    rejected_private: int = 0
    rejected_quasi_public: int = 0
    rejected_nonprofit: int = 0
    rejected_public_private: int = 0
    rejected_not_new_taipei: int = 0
    rejected_unknown_type: int = 0
    rejected_missing_address: int = 0
    rejected_duplicate: int = 0
    rejected_other: int = 0

    # Valid breakdown
    valid_by_district: dict[str, int] = field(default_factory=dict)
    valid_by_public_type: dict[str, int] = field(default_factory=dict)

    needs_review_count: int = 0

    def increment_rejection(self, reason: str) -> None:
        """Increment the appropriate rejection counter."""
        mapping = {
            "private": "rejected_private",
            "quasi_public": "rejected_quasi_public",
            "nonprofit": "rejected_nonprofit",
            "public_private_partnership": "rejected_public_private",
            "not_new_taipei": "rejected_not_new_taipei",
            "unknown_type": "rejected_unknown_type",
            "missing_address": "rejected_missing_address",
            "duplicate": "rejected_duplicate",
        }
        attr = mapping.get(reason, "rejected_other")
        setattr(self, attr, getattr(self, attr) + 1)
        self.total_rejected += 1

    def add_valid(self, district: Optional[str], public_type: PublicType) -> None:
        d = district or "未知"
        self.valid_by_district[d] = self.valid_by_district.get(d, 0) + 1
        t = public_type.value
        self.valid_by_public_type[t] = self.valid_by_public_type.get(t, 0) + 1
        self.total_valid += 1

    def print_summary(self) -> None:
        """Print a human-readable summary to stdout."""
        print("\n" + "=" * 60)
        print("  VALIDATION REPORT")
        print("=" * 60)
        print(f"  Total input records:      {self.total_input:>6}")
        print(f"  Valid (public + NTC):      {self.total_valid:>6}")
        print(f"  Rejected:                  {self.total_rejected:>6}")
        print(f"  Needs review:              {self.needs_review_count:>6}")
        print("-" * 60)
        print("  REJECTION BREAKDOWN:")
        print(f"    Private (私立):           {self.rejected_private:>6}")
        print(f"    Quasi-public (準公共):    {self.rejected_quasi_public:>6}")
        print(f"    Nonprofit (非營利):       {self.rejected_nonprofit:>6}")
        print(f"    Public-private (公設民營):{self.rejected_public_private:>6}")
        print(f"    Not New Taipei City:      {self.rejected_not_new_taipei:>6}")
        print(f"    Unknown type:             {self.rejected_unknown_type:>6}")
        print(f"    Missing address:          {self.rejected_missing_address:>6}")
        print(f"    Duplicate:                {self.rejected_duplicate:>6}")
        print(f"    Other:                    {self.rejected_other:>6}")
        print("-" * 60)
        print("  VALID BY PUBLIC TYPE:")
        for ptype, count in sorted(self.valid_by_public_type.items()):
            print(f"    {ptype:<25} {count:>6}")
        print("-" * 60)
        print("  VALID BY DISTRICT (新北市公立幼兒園):")
        for district, count in sorted(
            self.valid_by_district.items(), key=lambda x: -x[1]
        ):
            print(f"    {district:<12} {count:>6}")
        print("=" * 60)


# ---------------------------------------------------------------------------
# Validation logic
# ---------------------------------------------------------------------------


def _check_city(record: dict) -> bool:
    """
    Return True if the record is clearly from New Taipei City.

    Checks address field and _raw_city field.
    """
    raw_city = record.get("_raw_city", "") or ""
    address = record.get("address", "") or ""
    district = record.get("district", "") or ""

    ntc = settings.NEW_TAIPEI_CITY

    if ntc in raw_city:
        return True
    if ntc in address:
        return True
    if district in settings.NEW_TAIPEI_DISTRICTS:
        return True

    # If city is explicitly a different city, reject
    other_cities = ["臺北市", "台北市", "桃園市", "基隆市", "宜蘭縣"]
    for city in other_cities:
        if city in raw_city or city in address:
            return False

    # Ambiguous — if address contains a NTC district keyword, accept
    for d in settings.NEW_TAIPEI_DISTRICTS:
        if d in address:
            return True

    return False


def validate_record(record: dict) -> ValidationResult:
    """
    Validate a single kindergarten record.

    Checks (in order):
    1. Public type — reject non-public immediately
    2. City — must be New Taipei City
    3. Address — must have a non-empty address

    Returns:
        ValidationResult with is_valid and rejection_reason.
    """
    public_type = record.get("public_type", PublicType.UNKNOWN)

    # --- Step 1: Public type ---
    if not is_truly_public(public_type):
        reason = get_rejection_reason(public_type) or "unknown_type"
        return ValidationResult(
            is_valid=False,
            rejection_reason=reason,
            notes=f"original_type={record.get('_raw_type', '')}",
        )

    # --- Step 2: City must be New Taipei City ---
    if not _check_city(record):
        return ValidationResult(
            is_valid=False,
            rejection_reason="not_new_taipei",
            notes=f"address={record.get('address', '')} city={record.get('_raw_city', '')}",
        )

    # --- Step 3: Must have an address ---
    if not record.get("address"):
        return ValidationResult(
            is_valid=False,
            rejection_reason="missing_address",
            notes="address field is empty",
        )

    return ValidationResult(is_valid=True)


# ---------------------------------------------------------------------------
# Dedup check
# ---------------------------------------------------------------------------


def _dedup_records(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Remove duplicate records (same official_name + address).

    Returns:
        (unique_records, duplicate_records)
    """
    seen: dict[str, dict] = {}
    unique: list[dict] = []
    duplicates: list[dict] = []

    for record in records:
        name = (record.get("official_name") or "").strip()
        addr = (record.get("address") or "").strip()
        key = f"{name}||{addr}"

        if key in seen:
            duplicates.append(record)
            logger.debug(
                f"Duplicate found: {name}",
                extra={"action": "dedup", "status": "duplicate", "kindergarten": name},
            )
        else:
            seen[key] = record
            unique.append(record)

    return unique, duplicates


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


_VALID_CSV_FIELDS = [
    "official_name", "school_name", "kindergarten_name",
    "public_type", "is_public", "district", "address", "phone",
    "latitude", "longitude", "official_source", "official_source_id",
    "status",
]

_REJECTED_CSV_FIELDS = [
    "name", "reason", "original_type", "address", "source",
]


def _write_valid_csv(records: list[dict], path: Path) -> None:
    """Write valid kindergarten records to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=_VALID_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            row = {k: r.get(k, "") for k in _VALID_CSV_FIELDS}
            if isinstance(row.get("public_type"), PublicType):
                row["public_type"] = row["public_type"].value
            if isinstance(row.get("status"), KindergartenStatus):
                row["status"] = row["status"].value
            writer.writerow(row)


def _write_rejected_csv(records: list[dict], path: Path) -> None:
    """Write rejected kindergarten records to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=_REJECTED_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow(r)


# ---------------------------------------------------------------------------
# Main validator class
# ---------------------------------------------------------------------------


class PublicKindergartenValidator:
    """
    Validates a list of kindergarten records and separates:
    - valid: New Taipei City public kindergartens
    - rejected: everything else (with reason)

    Also persists results to CSV files in data/validation/.
    """

    def __init__(self) -> None:
        self.valid_csv_path = settings.validation_dir / "public_kindergartens_valid.csv"
        self.rejected_csv_path = settings.validation_dir / "rejected_kindergartens.csv"

    def run(
        self,
        records: list[dict],
        write_csv: bool = True,
    ) -> tuple[list[dict], list[dict], ValidationReport]:
        """
        Run validation on a list of records.

        Args:
            records: List of record dicts from OfficialKindergartenCollector.
            write_csv: If True, write results to CSV files.

        Returns:
            (valid_records, rejected_records, report)
        """
        report = ValidationReport(total_input=len(records))

        # Dedup first
        records, dup_records = _dedup_records(records)
        for r in dup_records:
            reject_row = {
                "name": r.get("official_name", ""),
                "reason": "duplicate",
                "original_type": (r.get("public_type") or PublicType.UNKNOWN).value
                if hasattr(r.get("public_type"), "value")
                else str(r.get("public_type", "")),
                "address": r.get("address", ""),
                "source": r.get("official_source", ""),
            }
            report.increment_rejection("duplicate")

        valid_records: list[dict] = []
        rejected_records: list[dict] = []

        for record in records:
            result = validate_record(record)

            if result.is_valid:
                # Ensure district is set
                if not record.get("district"):
                    record["district"] = extract_district(record.get("address", ""))
                record["status"] = KindergartenStatus.ACTIVE
                valid_records.append(record)
                report.add_valid(record.get("district"), record.get("public_type", PublicType.PUBLIC))
                logger.debug(
                    f"VALID: {record.get('official_name')}",
                    extra={
                        "action": "validate",
                        "status": "valid",
                        "kindergarten": record.get("official_name", "-"),
                    },
                )
            else:
                # Check if needs_review
                if result.rejection_reason == "unknown_type":
                    record["status"] = KindergartenStatus.NEEDS_REVIEW
                    report.needs_review_count += 1
                else:
                    record["status"] = KindergartenStatus.REJECTED

                rejected_row = {
                    "name": record.get("official_name", ""),
                    "reason": result.rejection_reason or "unknown",
                    "original_type": str(record.get("_raw_type", "")),
                    "address": record.get("address", ""),
                    "source": record.get("official_source", ""),
                }
                rejected_records.append(rejected_row)
                report.increment_rejection(result.rejection_reason or "unknown")
                logger.debug(
                    f"REJECTED [{result.rejection_reason}]: {record.get('official_name')}",
                    extra={
                        "action": "validate",
                        "status": "rejected",
                        "kindergarten": record.get("official_name", "-"),
                    },
                )

        # Also add dedup rejects to the rejected list
        for r in dup_records:
            rejected_records.append({
                "name": r.get("official_name", ""),
                "reason": "duplicate",
                "original_type": str(r.get("_raw_type", "")),
                "address": r.get("address", ""),
                "source": r.get("official_source", ""),
            })

        if write_csv:
            settings.validation_dir.mkdir(parents=True, exist_ok=True)
            _write_valid_csv(valid_records, self.valid_csv_path)
            _write_rejected_csv(rejected_records, self.rejected_csv_path)
            logger.info(
                f"Wrote {len(valid_records)} valid / {len(rejected_records)} rejected to CSV",
                extra={"action": "validate", "status": "csv_written", "kindergarten": "-"},
            )

        return valid_records, rejected_records, report
