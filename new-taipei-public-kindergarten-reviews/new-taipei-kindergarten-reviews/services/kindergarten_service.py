"""
Kindergarten service — orchestrates import and validation.

Bridges collectors, validators, and repository.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from analysis.public_kindergarten_validator import (
    PublicKindergartenValidator,
    ValidationReport,
)
from collectors.official_kindergartens import OfficialKindergartenCollector
from config.settings import settings
from database.models import KindergartenStatus, PublicType
from database.repository import (
    KindergartenRepository,
    RejectedKindergartenRepository,
)
from utils.logging import get_logger

logger = get_logger(__name__)


class KindergartenService:
    """
    Orchestrates kindergarten import and validation pipeline.

    Responsibilities:
    - Collect raw records from official sources
    - Validate (public type + city check)
    - Persist valid records to kindergartens table
    - Persist rejected records to rejected_kindergartens table
    - Write validation CSVs
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.kg_repo = KindergartenRepository(session)
        self.rejected_repo = RejectedKindergartenRepository(session)
        self.collector = OfficialKindergartenCollector()
        self.validator = PublicKindergartenValidator()

    def import_kindergartens(
        self,
        csv_file: Optional[Path] = None,
        use_seed: bool = True,
        try_moe_api: bool = False,
    ) -> dict:
        """
        Full import pipeline: collect -> persist (without validation filter).

        Imports ALL records from the source (public AND non-public).
        Use validate_kindergartens() afterward to apply filters.

        Args:
            csv_file: Optional path to a local CSV to import.
            use_seed: Use built-in seed data if no other source.
            try_moe_api: Attempt MOE open data download.

        Returns:
            Dict with import statistics.
        """
        logger.info(
            "Starting kindergarten import",
            extra={"action": "import", "status": "start", "kindergarten": "-"},
        )

        records = self.collector.collect(
            csv_file=csv_file,
            use_seed=use_seed,
            try_moe_api=try_moe_api,
        )

        inserted = 0
        updated = 0
        for record in records:
            # Strip internal keys before DB insert
            db_record = {k: v for k, v in record.items() if not k.startswith("_")}
            _, created = self.kg_repo.upsert(db_record)
            if created:
                inserted += 1
            else:
                updated += 1

        self.session.commit()
        logger.info(
            f"Import complete: {inserted} inserted, {updated} updated",
            extra={"action": "import", "status": "done", "kindergarten": "-"},
        )
        return {
            "total": len(records),
            "inserted": inserted,
            "updated": updated,
        }

    def validate_kindergartens(self, write_csv: bool = True) -> ValidationReport:
        """
        Validate all imported kindergartens and update their status.

        - Sets status=ACTIVE for valid public NTC kindergartens
        - Sets status=REJECTED for non-public / wrong city
        - Sets status=NEEDS_REVIEW for unknown type
        - Saves rejected records to rejected_kindergartens table
        - Writes validation CSVs

        Returns:
            ValidationReport with statistics.
        """
        logger.info(
            "Starting kindergarten validation",
            extra={"action": "validate", "status": "start", "kindergarten": "-"},
        )

        from sqlalchemy import select
        from database.models import Kindergarten

        # Load all records from DB as dicts for validator
        all_kg = self.session.scalars(select(Kindergarten)).all()

        records = []
        for kg in all_kg:
            records.append({
                "id": kg.id,
                "official_name": kg.official_name,
                "school_name": kg.school_name,
                "kindergarten_name": kg.kindergarten_name,
                "public_type": kg.public_type,
                "is_public": kg.is_public,
                "district": kg.district,
                "address": kg.address,
                "official_source": kg.official_source,
                "official_source_id": kg.official_source_id,
                "_raw_city": "新北市" if (kg.address and "新北市" in kg.address) else "",
                "_raw_type": kg.public_type.value if kg.public_type else "",
            })

        valid_records, rejected_records, report = self.validator.run(
            records, write_csv=write_csv
        )

        # Update DB status for valid records
        valid_ids = {r["id"] for r in valid_records if r.get("id")}
        for kg in all_kg:
            if kg.id in valid_ids:
                kg.status = KindergartenStatus.ACTIVE
                kg.is_public = True
            elif kg.public_type in (PublicType.UNKNOWN,):
                kg.status = KindergartenStatus.NEEDS_REVIEW
            else:
                kg.status = KindergartenStatus.REJECTED
                kg.is_public = False

        # Persist rejected records
        self.rejected_repo.get_all()  # Clear any stale (idempotent: upsert not needed for rejects)
        for reject_row in rejected_records:
            self.rejected_repo.add(reject_row)

        self.session.commit()

        logger.info(
            f"Validation complete: {report.total_valid} valid, {report.total_rejected} rejected",
            extra={"action": "validate", "status": "done", "kindergarten": "-"},
        )
        return report
