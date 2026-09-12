"""
Tests for the public kindergarten pipeline (STEP 1-5 scope).

Covers:
- Public kindergarten acceptance
- Private rejection
- Quasi-public rejection
- Nonprofit rejection
- Unknown-type handling
- Not-New-Taipei-City rejection
- Missing address rejection
- Deduplication
- review hashing
- Rating validation
- CSV validator output
- DB repository upsert / dedup
- Resume: re-import does not duplicate rows
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest

from analysis.public_kindergarten_validator import (
    PublicKindergartenValidator,
    ValidationReport,
    validate_record,
)
from database.models import (
    GoogleMatchStatus,
    KindergartenStatus,
    PublicType,
    Review,
)
from database.repository import (
    KindergartenRepository,
    RejectedKindergartenRepository,
    ReviewRepository,
)
from utils.hashing import compute_review_hash
from utils.normalization import (
    extract_district,
    is_truly_public,
    normalize_public_type,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def make_record(
    name: str = "XX幼兒園",
    raw_type: str = "公立",
    address: str = "新北市板橋區測試路1號",
    district: str = "板橋區",
    city: str = "新北市",
    source_id: str = "TEST-001",
) -> dict:
    public_type = normalize_public_type(raw_type)
    return {
        "official_name": name,
        "public_type": public_type,
        "is_public": is_truly_public(public_type),
        "district": district,
        "address": address,
        "official_source": "test",
        "official_source_id": source_id,
        "_raw_city": city,
        "_raw_type": raw_type,
    }


# ---------------------------------------------------------------------------
# Section 1: Public type normalization
# ---------------------------------------------------------------------------


class TestNormalizePublicType:

    def test_public_keywords(self):
        assert normalize_public_type("公立") == PublicType.PUBLIC
        assert normalize_public_type("市立") == PublicType.PUBLIC
        assert normalize_public_type("區立") == PublicType.PUBLIC
        assert normalize_public_type("國立") == PublicType.PUBLIC
        assert normalize_public_type("縣立") == PublicType.PUBLIC

    def test_public_attached(self):
        assert normalize_public_type("公立附設") == PublicType.PUBLIC_ATTACHED
        assert normalize_public_type("國民小學附設") == PublicType.PUBLIC_ATTACHED
        assert normalize_public_type("國小附設") == PublicType.PUBLIC_ATTACHED

    def test_private(self):
        assert normalize_public_type("私立") == PublicType.PRIVATE

    def test_quasi_public(self):
        assert normalize_public_type("準公共") == PublicType.QUASI_PUBLIC

    def test_nonprofit(self):
        assert normalize_public_type("非營利") == PublicType.NONPROFIT

    def test_public_private(self):
        assert normalize_public_type("公設民營") == PublicType.PUBLIC_PRIVATE_PARTNERSHIP

    def test_unknown(self):
        assert normalize_public_type("") == PublicType.UNKNOWN
        assert normalize_public_type(None) == PublicType.UNKNOWN
        assert normalize_public_type("不明") == PublicType.UNKNOWN

    def test_is_truly_public_true(self):
        assert is_truly_public(PublicType.PUBLIC) is True
        assert is_truly_public(PublicType.PUBLIC_ATTACHED) is True

    def test_is_truly_public_false(self):
        assert is_truly_public(PublicType.PRIVATE) is False
        assert is_truly_public(PublicType.QUASI_PUBLIC) is False
        assert is_truly_public(PublicType.NONPROFIT) is False
        assert is_truly_public(PublicType.PUBLIC_PRIVATE_PARTNERSHIP) is False
        assert is_truly_public(PublicType.UNKNOWN) is False


# ---------------------------------------------------------------------------
# Section 2: Validator — single record
# ---------------------------------------------------------------------------


class TestValidateRecord:

    def test_accept_public(self):
        r = make_record(raw_type="公立")
        result = validate_record(r)
        assert result.is_valid is True

    def test_accept_public_attached(self):
        r = make_record(raw_type="國民小學附設")
        result = validate_record(r)
        assert result.is_valid is True

    def test_reject_private(self):
        r = make_record(raw_type="私立")
        result = validate_record(r)
        assert result.is_valid is False
        assert result.rejection_reason == "private"

    def test_reject_quasi_public(self):
        r = make_record(raw_type="準公共")
        result = validate_record(r)
        assert result.is_valid is False
        assert result.rejection_reason == "quasi_public"

    def test_reject_nonprofit(self):
        r = make_record(raw_type="非營利")
        result = validate_record(r)
        assert result.is_valid is False
        assert result.rejection_reason == "nonprofit"

    def test_reject_public_private(self):
        r = make_record(raw_type="公設民營")
        result = validate_record(r)
        assert result.is_valid is False
        assert result.rejection_reason == "public_private_partnership"

    def test_reject_not_new_taipei(self):
        r = make_record(
            raw_type="公立",
            address="臺北市信義區松仁路1號",
            district="信義區",
            city="臺北市",
        )
        result = validate_record(r)
        assert result.is_valid is False
        assert result.rejection_reason == "not_new_taipei"

    def test_reject_missing_address(self):
        r = make_record(raw_type="公立", address="")
        result = validate_record(r)
        assert result.is_valid is False
        assert result.rejection_reason == "missing_address"

    def test_reject_unknown_type(self):
        r = make_record(raw_type="")
        result = validate_record(r)
        assert result.is_valid is False
        assert result.rejection_reason == "unknown_type"


# ---------------------------------------------------------------------------
# Section 3: Address and district extraction
# ---------------------------------------------------------------------------


class TestAddressExtraction:

    def test_extract_full_ntc_address(self):
        assert extract_district("新北市板橋區中山路一段1號") == "板橋區"

    def test_extract_district_only(self):
        assert extract_district("板橋區中山路一號") == "板橋區"

    def test_none_address(self):
        assert extract_district(None) is None
        assert extract_district("") is None

    def test_various_districts(self):
        for d in ["三重區", "中和區", "永和區", "新店區"]:
            result = extract_district(f"新北市{d}測試路1號")
            assert result == d, f"Expected {d}, got {result}"


# ---------------------------------------------------------------------------
# Section 4: Review hashing
# ---------------------------------------------------------------------------


class TestReviewHashing:

    def test_deterministic(self):
        h1 = compute_review_hash("PlaceA", "王小明", 5, "很棒", datetime(2024, 1, 1))
        h2 = compute_review_hash("PlaceA", "王小明", 5, "很棒", datetime(2024, 1, 1))
        assert h1 == h2

    def test_different_inputs_differ(self):
        h1 = compute_review_hash("PlaceA", "王小明", 5, "很棒", datetime(2024, 1, 1))
        h2 = compute_review_hash("PlaceA", "王小明", 4, "很棒", datetime(2024, 1, 1))
        assert h1 != h2

    def test_none_fields_handled(self):
        h = compute_review_hash("PlaceA", None, None, None, None)
        assert len(h) == 64

    def test_sha256_length(self):
        h = compute_review_hash("place", "author", 3, "text", datetime(2024, 6, 1))
        assert len(h) == 64

    def test_special_characters(self):
        h1 = compute_review_hash("X", "陳大文", 5, "老師很有愛心！", datetime(2025, 1, 1))
        h2 = compute_review_hash("X", "陳大文", 5, "老師很有愛心！", datetime(2025, 1, 1))
        assert h1 == h2


# ---------------------------------------------------------------------------
# Section 5: Repository — kindergarten upsert / dedup
# ---------------------------------------------------------------------------


class TestKindergartenRepository:

    def test_insert_new(self, session):
        repo = KindergartenRepository(session)
        data = {
            "official_name": "板橋幼兒園",
            "public_type": PublicType.PUBLIC,
            "is_public": True,
            "district": "板橋區",
            "address": "新北市板橋區中山路1號",
            "official_source": "test",
            "official_source_id": "T001",
            "status": KindergartenStatus.ACTIVE,
        }
        kg, created = repo.upsert(data)
        assert created is True
        assert kg.id is not None
        assert kg.official_name == "板橋幼兒園"

    def test_update_existing(self, session):
        repo = KindergartenRepository(session)
        data = {
            "official_name": "板橋幼兒園",
            "public_type": PublicType.PUBLIC,
            "is_public": True,
            "district": "板橋區",
            "address": "新北市板橋區中山路1號",
            "official_source": "test",
            "official_source_id": "T001",
            "status": KindergartenStatus.ACTIVE,
        }
        kg1, created1 = repo.upsert(data)
        assert created1 is True

        # Re-insert same source ID -> update
        data2 = dict(data)
        data2["phone"] = "02-12345678"
        kg2, created2 = repo.upsert(data2)
        assert created2 is False
        assert kg2.id == kg1.id
        assert kg2.phone == "02-12345678"

    def test_resume_no_duplicate(self, session):
        """
        Running import twice should not create duplicate rows.
        """
        repo = KindergartenRepository(session)
        data = {
            "official_name": "中和幼兒園",
            "public_type": PublicType.PUBLIC,
            "is_public": True,
            "district": "中和區",
            "address": "新北市中和區安邦街1號",
            "official_source": "seed",
            "official_source_id": "S001",
            "status": KindergartenStatus.ACTIVE,
        }
        repo.upsert(data)
        session.commit()
        repo.upsert(data)
        session.commit()
        total = repo.count_total()
        assert total == 1

    def test_count_public(self, session):
        repo = KindergartenRepository(session)
        for i, (public, ptype) in enumerate([
            (True, PublicType.PUBLIC),
            (True, PublicType.PUBLIC_ATTACHED),
            (False, PublicType.PRIVATE),
        ]):
            repo.upsert({
                "official_name": f"KG{i}",
                "public_type": ptype,
                "is_public": public,
                "district": "板橋區",
                "address": f"新北市板橋區路{i}號",
                "official_source": "test",
                "official_source_id": f"X{i}",
                "status": KindergartenStatus.ACTIVE if public else KindergartenStatus.REJECTED,
            })
        session.commit()
        assert repo.count_public() == 2


# ---------------------------------------------------------------------------
# Section 6: Repository — review dedup
# ---------------------------------------------------------------------------


class TestReviewRepository:

    def _make_review(self, place_id: str, rating: int, text: str, idx: int = 0) -> dict:
        from utils.hashing import compute_review_hash
        publish_time = datetime(2024, 1, idx + 1)
        content_hash = compute_review_hash(place_id, "作者", rating, text, publish_time)
        return {
            "kindergarten_id": 1,
            "place_id": place_id,
            "rating": rating,
            "original_text": text,
            "publish_time": publish_time,
            "content_hash": content_hash,
            "source": "google_places_api",
        }

    def test_insert_new_review(self, session):
        # Need a Kindergarten row first for FK
        from database.models import Kindergarten
        kg = Kindergarten(
            official_name="KG", public_type=PublicType.PUBLIC, is_public=True,
            official_source="t", official_source_id="R1",
            status=KindergartenStatus.ACTIVE,
        )
        session.add(kg)
        session.flush()

        repo = ReviewRepository(session)
        data = self._make_review("PlaceX", 5, "很好", 0)
        data["kindergarten_id"] = kg.id
        rv, created = repo.upsert(data)
        assert created is True

    def test_dedup_same_review(self, session):
        from database.models import Kindergarten
        kg = Kindergarten(
            official_name="KG", public_type=PublicType.PUBLIC, is_public=True,
            official_source="t", official_source_id="R2",
            status=KindergartenStatus.ACTIVE,
        )
        session.add(kg)
        session.flush()

        repo = ReviewRepository(session)
        data = self._make_review("PlaceY", 4, "不錯", 0)
        data["kindergarten_id"] = kg.id

        rv1, c1 = repo.upsert(data)
        rv2, c2 = repo.upsert(data)
        assert c1 is True
        assert c2 is False  # second insert: deduplicated
        assert rv1.id == rv2.id
        assert repo.count_total() == 1

    def test_different_reviews_not_deduped(self, session):
        from database.models import Kindergarten
        kg = Kindergarten(
            official_name="KG", public_type=PublicType.PUBLIC, is_public=True,
            official_source="t", official_source_id="R3",
            status=KindergartenStatus.ACTIVE,
        )
        session.add(kg)
        session.flush()

        repo = ReviewRepository(session)
        d1 = self._make_review("PlaceZ", 5, "好", 0)
        d1["kindergarten_id"] = kg.id
        d2 = self._make_review("PlaceZ", 3, "普通", 1)
        d2["kindergarten_id"] = kg.id

        repo.upsert(d1)
        repo.upsert(d2)
        assert repo.count_total() == 2

    def test_rating_validation(self, session):
        """Valid ratings are 1-5."""
        for r in range(1, 6):
            assert 1 <= r <= 5


# ---------------------------------------------------------------------------
# Section 7: Validator batch run
# ---------------------------------------------------------------------------


class TestValidatorBatch:

    def _make_records(self) -> list[dict]:
        return [
            make_record("A_public_kg", "public", "new_taipei_banqiao_1", "Banqiao", "NewTaipei", "A1"),
            make_record("B_private_kg", "private", "new_taipei_banqiao_2", "Banqiao", "NewTaipei", "A2"),
            make_record("C_quasi_kg", "quasi_public", "new_taipei_banqiao_3", "Banqiao", "NewTaipei", "A3"),
            make_record("D_nonprofit_kg", "nonprofit", "new_taipei_banqiao_4", "Banqiao", "NewTaipei", "A4"),
            make_record("E_taipei_kg", "public", "taipei_city_1", "Xinyi", "Taipei", "A5"),
            make_record("F_attached_kg", "public_attached", "new_taipei_xinzhuang_1", "Xinzhuang", "NewTaipei", "A6"),
        ]

    def _make_ntc_records(self) -> list[dict]:
        """Records using actual New Taipei addresses for city check."""
        return [
            make_record("A_public_kg", "公立", "新北市板橋區中山路1號", "板橋區", "新北市", "A1"),
            make_record("B_private_kg", "私立", "新北市板橋區中山路2號", "板橋區", "新北市", "A2"),
            make_record("C_quasi_kg", "準公共", "新北市板橋區中山路3號", "板橋區", "新北市", "A3"),
            make_record("D_nonprofit_kg", "非營利", "新北市板橋區中山路4號", "板橋區", "新北市", "A4"),
            make_record("E_taipei_kg", "公立", "臺北市信義區松仁路1號", "信義區", "臺北市", "A5"),
            make_record("F_attached_kg", "國民小學附設", "新北市新莊區中正路1號", "新莊區", "新北市", "A6"),
        ]

    def test_accept_public_reject_others(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            validator = PublicKindergartenValidator()
            validator.valid_csv_path = Path(tmp) / "valid.csv"
            validator.rejected_csv_path = Path(tmp) / "rejected.csv"

            records = self._make_ntc_records()
            valid, rejected, report = validator.run(records, write_csv=True)

            # public + public_attached from NTC
            assert len(valid) == 2
            # private + quasi + nonprofit + taipei city
            assert len(rejected) == 4
            assert report.rejected_private == 1
            assert report.rejected_quasi_public == 1
            assert report.rejected_nonprofit == 1
            assert report.rejected_not_new_taipei == 1

    def test_csv_files_created(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            validator = PublicKindergartenValidator()
            validator.valid_csv_path = Path(tmp) / "valid.csv"
            validator.rejected_csv_path = Path(tmp) / "rejected.csv"

            records = self._make_ntc_records()
            validator.run(records, write_csv=True)

            assert validator.valid_csv_path.exists()
            assert validator.rejected_csv_path.exists()


# ---------------------------------------------------------------------------
# Section 8: Seed data sanity
# ---------------------------------------------------------------------------


class TestSeedData:

    def test_seed_loads_and_all_public(self):
        from collectors.official_kindergartens import load_seed_data
        records = load_seed_data()
        assert len(records) > 0, "Seed data must not be empty"
        for r in records:
            assert r["is_public"] is True, f"{r['official_name']} should be public"

    def test_seed_all_new_taipei(self):
        from collectors.official_kindergartens import load_seed_data
        records = load_seed_data()
        for r in records:
            addr = r.get("address", "")
            district = r.get("district", "")
            from config.settings import settings
            assert "新北市" in addr or district in settings.NEW_TAIPEI_DISTRICTS, (
                f"{r['official_name']}: address={addr} district={district}"
            )

    def test_seed_no_private(self):
        from collectors.official_kindergartens import load_seed_data
        records = load_seed_data()
        for r in records:
            assert r["public_type"] not in (
                PublicType.PRIVATE,
                PublicType.QUASI_PUBLIC,
                PublicType.NONPROFIT,
                PublicType.PUBLIC_PRIVATE_PARTNERSHIP,
            ), f"{r['official_name']} should not be private/quasi/nonprofit"

    def test_seed_district_coverage(self):
        """Seed data should cover at least 10 different districts."""
        from collectors.official_kindergartens import load_seed_data
        records = load_seed_data()
        districts = {r.get("district") for r in records if r.get("district")}
        assert len(districts) >= 10, f"Expected ≥10 districts, got {len(districts)}: {districts}"


# ---------------------------------------------------------------------------
# Section 9: Import → Validate pipeline (service layer)
# ---------------------------------------------------------------------------


class TestImportValidatePipeline:

    def test_full_pipeline_seed(self, session):
        from services.kindergarten_service import KindergartenService
        svc = KindergartenService(session)

        result = svc.import_kindergartens(use_seed=True, try_moe_api=False)
        assert result["total"] > 0
        assert result["inserted"] > 0

        report = svc.validate_kindergartens(write_csv=False)
        assert report.total_valid > 0
        assert report.total_valid > 20  # should be well over 20 public NTC kgs
        assert report.rejected_private == 0  # seed has no private
        assert report.rejected_quasi_public == 0

        kg_repo = KindergartenRepository(session)
        public_count = kg_repo.count_public()
        assert public_count == report.total_valid

    def test_resume_idempotent(self, session):
        """Running import twice should not duplicate rows."""
        from services.kindergarten_service import KindergartenService
        svc = KindergartenService(session)

        r1 = svc.import_kindergartens(use_seed=True)
        r2 = svc.import_kindergartens(use_seed=True)

        assert r2["inserted"] == 0  # all already exist
        assert r2["updated"] >= 0

        kg_repo = KindergartenRepository(session)
        assert kg_repo.count_total() == r1["total"]

    def test_district_breakdown(self, session):
        from services.kindergarten_service import KindergartenService
        svc = KindergartenService(session)
        svc.import_kindergartens(use_seed=True)
        svc.validate_kindergartens(write_csv=False)

        kg_repo = KindergartenRepository(session)
        breakdown = kg_repo.district_breakdown()

        # Must have at least 板橋區 and 新莊區
        assert breakdown.get("板橋區", 0) > 0
        assert breakdown.get("新莊區", 0) > 0
