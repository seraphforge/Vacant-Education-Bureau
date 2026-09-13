"""
analysis/data_quality.py
------------------------
Checks all three source databases (official_kindergartens, google_places,
reviews) plus the master database for data quality issues and reports them
as structured :class:`QualityIssue` objects.

Issue severity ladder
---------------------
  CRITICAL  — data integrity violation; pipeline output is unreliable.
  HIGH      — data will likely produce wrong analytics / missing records.
  WARNING   — data is suspicious; may affect downstream quality.
  INFO      — informational notices; no immediate action required.

Issue types
-----------
  PRIVATE_CONTAMINATION       A non-public record stored with is_public=1
  QUASI_PUBLIC_CONTAMINATION  A quasi-public record stored with is_public=1
  NONPROFIT_CONTAMINATION     A non-profit record stored with is_public=1
  MISSING_DISTRICT            district is NULL, empty, or "UNKNOWN"
  INVALID_DISTRICT            district not in the 29 valid New Taipei districts
  DUPLICATE_KINDERGARTEN      Same (official_name, district) appears > once
  DUPLICATE_PLACE_ID          Same google_place_id assigned to multiple kindergartens
  DISTRICT_MISMATCH           Place match district differs from official record district
  DUPLICATE_REVIEW            Same content_hash appears > once
  INVALID_RATING              Rating outside 1–5
  MISSING_ADDRESS             address is NULL or empty
  MISSING_PLACE               A PUBLIC kindergarten has no place match
  INVALID_COORDINATE          lat/lng outside Taiwan bounds (lat 21–26, lng 119–123)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Taiwan geographic bounds
# ---------------------------------------------------------------------------

TAIWAN_LAT_MIN = 21.0
TAIWAN_LAT_MAX = 26.0
TAIWAN_LNG_MIN = 119.0
TAIWAN_LNG_MAX = 123.0

# ---------------------------------------------------------------------------
# QualityIssue dataclass
# ---------------------------------------------------------------------------


@dataclass
class QualityIssue:
    """Represents a single detected data quality problem."""

    issue_type: str
    severity: str  # CRITICAL | HIGH | WARNING | INFO
    description: str
    kindergarten_id: Optional[int] = None
    district: Optional[str] = None
    raw_value: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Severity constants
# ---------------------------------------------------------------------------

CRITICAL = "CRITICAL"
HIGH = "HIGH"
WARNING = "WARNING"
INFO = "INFO"

# ---------------------------------------------------------------------------
# DataQualityChecker
# ---------------------------------------------------------------------------


class DataQualityChecker:
    """Run data quality checks across all three source databases.

    Args:
        official_db_path: Path to ``official_kindergartens.db``.
                          Uses settings default when ``None``.
        places_db_path:   Path to ``google_places.db``.
                          Uses settings default when ``None``.
        reviews_db_path:  Path to ``reviews.db``.
                          Uses settings default when ``None``.
        master_db_path:   Path to ``kindergarten_reviews_master.db``.
                          Uses settings default when ``None``.
    """

    def __init__(
        self,
        official_db_path: Optional[str | Path] = None,
        places_db_path: Optional[str | Path] = None,
        reviews_db_path: Optional[str | Path] = None,
        master_db_path: Optional[str | Path] = None,
    ) -> None:
        self._official_db_path = official_db_path
        self._places_db_path = places_db_path
        self._reviews_db_path = reviews_db_path
        self._master_db_path = master_db_path

        # Cache rows so we only query each DB once per check_all() call
        self._official_rows: list[dict] | None = None
        self._places_rows: list[dict] | None = None
        self._reviews_rows: list[dict] | None = None

    # ------------------------------------------------------------------
    # Internal DB accessors
    # ------------------------------------------------------------------

    def _get_official_rows(self) -> list[dict]:
        if self._official_rows is None:
            from database import official_db  # noqa: PLC0415

            self._official_rows = official_db.get_all_kindergartens(
                self._official_db_path
            )
        return self._official_rows

    def _get_places_rows(self) -> list[dict]:
        if self._places_rows is None:
            from database import places_db  # noqa: PLC0415

            with places_db.get_connection(self._places_db_path) as conn:
                rows = (
                    places_db.place_matches_table.select()
                    .execute()
                    if False  # type: ignore[unreachable]  # use proper call below
                    else conn.execute(
                        places_db.place_matches_table.select()
                    ).mappings().all()
                )
                self._places_rows = [dict(r) for r in rows]
        return self._places_rows

    def _get_reviews_rows(self) -> list[dict]:
        if self._reviews_rows is None:
            from database import reviews_db  # noqa: PLC0415

            with reviews_db.get_connection(self._reviews_db_path) as conn:
                rows = conn.execute(
                    reviews_db.reviews_table.select()
                ).mappings().all()
                self._reviews_rows = [dict(r) for r in rows]
        return self._reviews_rows

    def _reset_cache(self) -> None:
        self._official_rows = None
        self._places_rows = None
        self._reviews_rows = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_all(self) -> list[QualityIssue]:
        """Run every check and return the combined list of issues.

        Returns:
            All detected :class:`QualityIssue` instances, grouped by severity
            (CRITICAL first).
        """
        self._reset_cache()
        issues: list[QualityIssue] = []
        issues.extend(self.check_private_contamination())
        issues.extend(self.check_district_issues())
        issues.extend(self.check_duplicates())
        issues.extend(self.check_rating_validity())
        issues.extend(self.check_missing_places())

        # Sort: CRITICAL → HIGH → WARNING → INFO
        severity_order = {CRITICAL: 0, HIGH: 1, WARNING: 2, INFO: 3}
        issues.sort(key=lambda i: severity_order.get(i.severity, 99))
        return issues

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def check_private_contamination(self) -> list[QualityIssue]:
        """Detect non-public kindergartens wrongly stored as is_public=1.

        Checks ``official_name``, ``school_name``, ``kindergarten_type``, and
        ``public_type`` using the project's :mod:`services.public_classifier`.

        Returns:
            List of CRITICAL :class:`QualityIssue` objects.
        """
        from services.public_classifier import classify_kindergarten, PublicType  # noqa: PLC0415

        issues: list[QualityIssue] = []

        _TYPE_TO_ISSUE = {
            PublicType.PRIVATE: "PRIVATE_CONTAMINATION",
            PublicType.QUASI_PUBLIC: "QUASI_PUBLIC_CONTAMINATION",
            PublicType.NONPROFIT: "NONPROFIT_CONTAMINATION",
        }

        for row in self._get_official_rows():
            if not row.get("is_public"):
                continue  # Already marked non-public — not a contamination issue

            actual_type = classify_kindergarten(
                kindergarten_type=row.get("kindergarten_type"),
                public_type=row.get("public_type"),
                official_name=row.get("official_name"),
                school_name=row.get("school_name"),
            )

            issue_type = _TYPE_TO_ISSUE.get(actual_type)
            if issue_type is None:
                continue  # PUBLIC or UNKNOWN — no contamination

            issues.append(
                QualityIssue(
                    issue_type=issue_type,
                    severity=CRITICAL,
                    description=(
                        f"Kindergarten id={row.get('id')} name='{row.get('official_name')}' "
                        f"is marked is_public=1 but classified as {actual_type.value}."
                    ),
                    kindergarten_id=row.get("id"),
                    district=row.get("district"),
                    raw_value=f"kindergarten_type={row.get('kindergarten_type')!r} "
                              f"public_type={row.get('public_type')!r}",
                )
            )

        return issues

    def check_district_issues(self) -> list[QualityIssue]:
        """Check for missing or invalid district values and coordinate issues.

        Covers:
          * MISSING_DISTRICT — district is NULL, empty string, or 'UNKNOWN'.
          * INVALID_DISTRICT — district not in the 29 valid New Taipei districts.
          * MISSING_ADDRESS  — address is NULL or empty.
          * INVALID_COORDINATE — lat/lng outside Taiwan bounds.

        Returns:
            List of HIGH/WARNING :class:`QualityIssue` objects.
        """
        from config.new_taipei_districts import NEW_TAIPEI_DISTRICTS_SET  # noqa: PLC0415

        issues: list[QualityIssue] = []

        for row in self._get_official_rows():
            kid_id = row.get("id")
            district = row.get("district") or ""
            district_str = str(district).strip()

            # MISSING_DISTRICT
            if not district_str or district_str.upper() == "UNKNOWN":
                issues.append(
                    QualityIssue(
                        issue_type="MISSING_DISTRICT",
                        severity=HIGH,
                        description=(
                            f"Kindergarten id={kid_id} name='{row.get('official_name')}' "
                            f"has missing or empty district."
                        ),
                        kindergarten_id=kid_id,
                        district=district_str or None,
                        raw_value=repr(district),
                    )
                )
            elif district_str not in NEW_TAIPEI_DISTRICTS_SET:
                # INVALID_DISTRICT
                issues.append(
                    QualityIssue(
                        issue_type="INVALID_DISTRICT",
                        severity=HIGH,
                        description=(
                            f"Kindergarten id={kid_id} name='{row.get('official_name')}' "
                            f"has district '{district_str}' which is not one of the "
                            f"29 valid New Taipei districts."
                        ),
                        kindergarten_id=kid_id,
                        district=district_str,
                        raw_value=district_str,
                    )
                )

            # MISSING_ADDRESS
            address = row.get("address") or ""
            if not str(address).strip():
                issues.append(
                    QualityIssue(
                        issue_type="MISSING_ADDRESS",
                        severity=WARNING,
                        description=(
                            f"Kindergarten id={kid_id} name='{row.get('official_name')}' "
                            f"has no address."
                        ),
                        kindergarten_id=kid_id,
                        district=district_str or None,
                        raw_value=repr(address),
                    )
                )

            # INVALID_COORDINATE
            lat = row.get("latitude")
            lng = row.get("longitude")
            if lat is not None and lng is not None:
                lat_ok = TAIWAN_LAT_MIN <= float(lat) <= TAIWAN_LAT_MAX
                lng_ok = TAIWAN_LNG_MIN <= float(lng) <= TAIWAN_LNG_MAX
                if not lat_ok or not lng_ok:
                    issues.append(
                        QualityIssue(
                            issue_type="INVALID_COORDINATE",
                            severity=WARNING,
                            description=(
                                f"Kindergarten id={kid_id} name='{row.get('official_name')}' "
                                f"has coordinates ({lat}, {lng}) outside Taiwan bounds "
                                f"(lat {TAIWAN_LAT_MIN}–{TAIWAN_LAT_MAX}, "
                                f"lng {TAIWAN_LNG_MIN}–{TAIWAN_LNG_MAX})."
                            ),
                            kindergarten_id=kid_id,
                            district=district_str or None,
                            raw_value=f"lat={lat}, lng={lng}",
                        )
                    )

        # Check place match rows for coordinate issues too
        for row in self._get_places_rows():
            kid_id = row.get("kindergarten_id")
            lat = row.get("google_latitude")
            lng = row.get("google_longitude")
            if lat is not None and lng is not None:
                lat_ok = TAIWAN_LAT_MIN <= float(lat) <= TAIWAN_LAT_MAX
                lng_ok = TAIWAN_LNG_MIN <= float(lng) <= TAIWAN_LNG_MAX
                if not lat_ok or not lng_ok:
                    issues.append(
                        QualityIssue(
                            issue_type="INVALID_COORDINATE",
                            severity=WARNING,
                            description=(
                                f"Place match for kindergarten_id={kid_id} "
                                f"(place_id={row.get('google_place_id')}) "
                                f"has coordinates ({lat}, {lng}) outside Taiwan bounds."
                            ),
                            kindergarten_id=kid_id,
                            district=row.get("district"),
                            raw_value=f"lat={lat}, lng={lng}",
                        )
                    )

            # DISTRICT_MISMATCH on place matches
            official_district = row.get("district") or ""
            google_address = row.get("google_address") or ""
            if (
                official_district
                and google_address
                and official_district not in google_address
                and row.get("match_status") not in ("REJECTED", "PENDING", None)
            ):
                issues.append(
                    QualityIssue(
                        issue_type="DISTRICT_MISMATCH",
                        severity=WARNING,
                        description=(
                            f"Place match for kindergarten_id={kid_id}: "
                            f"official district '{official_district}' not found in "
                            f"google_address '{google_address}'."
                        ),
                        kindergarten_id=kid_id,
                        district=official_district,
                        raw_value=google_address,
                    )
                )

        return issues

    def check_duplicates(self) -> list[QualityIssue]:
        """Detect duplicate kindergartens, place IDs, and review hashes.

        Checks:
          * DUPLICATE_KINDERGARTEN — same (official_name, district) > once.
          * DUPLICATE_PLACE_ID    — same google_place_id assigned to > 1 kindergarten.
          * DUPLICATE_REVIEW      — same content_hash in the reviews table > once.

        Returns:
            List of WARNING/INFO :class:`QualityIssue` objects.
        """
        issues: list[QualityIssue] = []

        # --- DUPLICATE_KINDERGARTEN -------------------------------------------
        seen_name_district: dict[tuple[str, str], list[int]] = {}
        for row in self._get_official_rows():
            name = (row.get("official_name") or "").strip()
            district = (row.get("district") or "").strip()
            key = (name, district)
            seen_name_district.setdefault(key, []).append(row.get("id"))  # type: ignore[arg-type]

        for (name, district), ids in seen_name_district.items():
            if len(ids) > 1:
                issues.append(
                    QualityIssue(
                        issue_type="DUPLICATE_KINDERGARTEN",
                        severity=WARNING,
                        description=(
                            f"official_name='{name}' in district='{district}' "
                            f"appears {len(ids)} times (ids={ids})."
                        ),
                        district=district,
                        raw_value=f"ids={ids}",
                    )
                )

        # --- DUPLICATE_PLACE_ID -----------------------------------------------
        seen_place_id: dict[str, list[int]] = {}
        for row in self._get_places_rows():
            place_id = row.get("google_place_id") or ""
            if not place_id:
                continue
            kid_id = row.get("kindergarten_id")
            seen_place_id.setdefault(place_id, []).append(kid_id)  # type: ignore[arg-type]

        for place_id, kid_ids in seen_place_id.items():
            # Deduplicate kindergarten_ids
            unique_ids = list(dict.fromkeys(kid_ids))
            if len(unique_ids) > 1:
                issues.append(
                    QualityIssue(
                        issue_type="DUPLICATE_PLACE_ID",
                        severity=WARNING,
                        description=(
                            f"google_place_id='{place_id}' is assigned to "
                            f"{len(unique_ids)} different kindergartens "
                            f"(kindergarten_ids={unique_ids})."
                        ),
                        raw_value=place_id,
                    )
                )

        # --- DUPLICATE_REVIEW -------------------------------------------------
        seen_hash: dict[str, list[int]] = {}
        for row in self._get_reviews_rows():
            content_hash = row.get("content_hash") or ""
            if not content_hash:
                continue
            seen_hash.setdefault(content_hash, []).append(row.get("id"))  # type: ignore[arg-type]

        for content_hash, review_ids in seen_hash.items():
            if len(review_ids) > 1:
                issues.append(
                    QualityIssue(
                        issue_type="DUPLICATE_REVIEW",
                        severity=INFO,
                        description=(
                            f"content_hash='{content_hash[:16]}…' "
                            f"appears {len(review_ids)} times "
                            f"(review ids={review_ids})."
                        ),
                        raw_value=content_hash,
                    )
                )

        return issues

    def check_rating_validity(self) -> list[QualityIssue]:
        """Check that all review ratings are integers in the range 1–5.

        Returns:
            List of HIGH :class:`QualityIssue` objects.
        """
        issues: list[QualityIssue] = []

        for row in self._get_reviews_rows():
            rating = row.get("rating")
            try:
                r = int(rating)  # type: ignore[arg-type]
                valid = 1 <= r <= 5
            except (TypeError, ValueError):
                valid = False

            if not valid:
                issues.append(
                    QualityIssue(
                        issue_type="INVALID_RATING",
                        severity=HIGH,
                        description=(
                            f"Review id={row.get('id')} for kindergarten_id="
                            f"{row.get('kindergarten_id')} has invalid rating={rating!r}. "
                            f"Expected integer 1–5."
                        ),
                        kindergarten_id=row.get("kindergarten_id"),
                        district=row.get("district"),
                        raw_value=repr(rating),
                    )
                )

        return issues

    def check_missing_places(self) -> list[QualityIssue]:
        """Identify PUBLIC kindergartens that have no Google Places match.

        Returns:
            List of INFO :class:`QualityIssue` objects.
        """
        issues: list[QualityIssue] = []

        # Build set of kindergarten_ids that have an accepted place match
        matched_ids: set[int] = set()
        for row in self._get_places_rows():
            if row.get("match_status") in ("AUTO_ACCEPT", "MANUAL_REVIEW", "override"):
                kid_id = row.get("kindergarten_id")
                if kid_id is not None:
                    matched_ids.add(int(kid_id))

        for row in self._get_official_rows():
            if not row.get("is_public"):
                continue
            kid_id = row.get("id")
            if kid_id not in matched_ids:
                issues.append(
                    QualityIssue(
                        issue_type="MISSING_PLACE",
                        severity=INFO,
                        description=(
                            f"PUBLIC kindergarten id={kid_id} "
                            f"name='{row.get('official_name')}' "
                            f"(district={row.get('district')}) "
                            f"has no accepted Google Places match."
                        ),
                        kindergarten_id=kid_id,
                        district=row.get("district"),
                        raw_value=row.get("official_name"),
                    )
                )

        return issues

    # ------------------------------------------------------------------
    # Persistence & reporting
    # ------------------------------------------------------------------

    def save_issues_to_master_db(
        self,
        issues: list[QualityIssue],
        db_path: Optional[str | Path] = None,
    ) -> None:
        """Persist quality issues to the ``data_quality_issues`` table.

        Existing unresolved issues of the same ``issue_type`` and
        ``kindergarten_id`` are cleared first so that re-running the checker
        reflects the current state of the data.

        Args:
            issues:  Issues returned by :meth:`check_all`.
            db_path: Override for the master DB path.
        """
        from database import master_db  # noqa: PLC0415
        from sqlalchemy import and_  # noqa: PLC0415

        effective_path = db_path or self._master_db_path

        with master_db.get_connection(effective_path) as conn:
            # Delete all *unresolved* issues so we get a clean slate on each run
            conn.execute(
                master_db.data_quality_issues_table.delete().where(
                    master_db.data_quality_issues_table.c.resolved == 0
                )
            )
            conn.commit()

        for issue in issues:
            master_db.log_quality_issue(
                issue_type=issue.issue_type,
                severity=issue.severity,
                description=issue.description,
                kindergarten_id=issue.kindergarten_id,
                district=issue.district,
                raw_value=issue.raw_value,
                created_at=issue.created_at,
                db_path=effective_path,
            )

        logger.info(
            "Saved %d quality issues to master DB.", len(issues)
        )

    def print_report(self, issues: list[QualityIssue]) -> None:
        """Print a rich-formatted quality report to the terminal.

        Groups issues by severity with colour coding:
          CRITICAL → bold red
          HIGH     → red
          WARNING  → yellow
          INFO     → cyan

        Args:
            issues: Issues returned by :meth:`check_all`.
        """
        try:
            from rich.console import Console  # noqa: PLC0415
            from rich.table import Table  # noqa: PLC0415
            from rich import box  # noqa: PLC0415
        except ImportError:
            # Fallback to plain text if rich is unavailable
            self._print_report_plain(issues)
            return

        console = Console()

        severity_styles = {
            CRITICAL: "bold red",
            HIGH: "red",
            WARNING: "yellow",
            INFO: "cyan",
        }

        # Summary counts
        counts: dict[str, int] = {CRITICAL: 0, HIGH: 0, WARNING: 0, INFO: 0}
        for issue in issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1

        console.rule("[bold]Data Quality Report[/bold]")
        console.print(
            f"  [bold red]CRITICAL: {counts[CRITICAL]}[/bold red]  "
            f"[red]HIGH: {counts[HIGH]}[/red]  "
            f"[yellow]WARNING: {counts[WARNING]}[/yellow]  "
            f"[cyan]INFO: {counts[INFO]}[/cyan]  "
            f"  Total: {len(issues)}"
        )
        console.print()

        if not issues:
            console.print("[bold green]✓ No issues found.[/bold green]")
            return

        table = Table(
            box=box.SIMPLE_HEAD,
            show_lines=False,
            expand=True,
        )
        table.add_column("Severity", style="bold", width=10, no_wrap=True)
        table.add_column("Type", width=28, no_wrap=True)
        table.add_column("ID", width=6, no_wrap=True)
        table.add_column("District", width=8, no_wrap=True)
        table.add_column("Description", ratio=1)

        for issue in issues:
            style = severity_styles.get(issue.severity, "white")
            table.add_row(
                f"[{style}]{issue.severity}[/{style}]",
                issue.issue_type,
                str(issue.kindergarten_id or ""),
                issue.district or "",
                issue.description,
                style=style if issue.severity == CRITICAL else "",
            )

        console.print(table)

    def _print_report_plain(self, issues: list[QualityIssue]) -> None:
        """Plain-text fallback for :meth:`print_report`."""
        print(f"\n=== Data Quality Report — {len(issues)} issue(s) ===\n")
        severity_order = {CRITICAL: 0, HIGH: 1, WARNING: 2, INFO: 3}
        for issue in sorted(issues, key=lambda i: severity_order.get(i.severity, 99)):
            print(
                f"[{issue.severity}] {issue.issue_type} "
                f"(kid_id={issue.kindergarten_id}, district={issue.district})"
            )
            print(f"  {issue.description}")
            if issue.raw_value:
                print(f"  raw: {issue.raw_value}")
            print()
