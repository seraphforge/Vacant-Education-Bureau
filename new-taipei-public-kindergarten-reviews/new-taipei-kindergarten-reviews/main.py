"""
New Taipei Public Kindergarten Google Review Intelligence Pipeline
CLI entry point.

Usage:
    python main.py import-kindergartens
    python main.py validate-kindergartens
    python main.py status
    python main.py match-places [--limit N]
    python main.py fetch-reviews [--limit N] [--district DISTRICT]
    python main.py retry-failed
    python main.py export

Commands implemented in STEP 1-5:
    import-kindergartens    Load official kindergarten data into database
    validate-kindergartens  Validate and filter to NTC public kindergartens
    status                  Show pipeline statistics

Commands available in STEP 6+:
    match-places            Match kindergartens to Google Places
    fetch-reviews           Fetch Google Place metadata and reviews
    retry-failed            Retry failed place/review fetches
    export                  Export data to CSV/JSON
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path when running directly
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from dotenv import load_dotenv

load_dotenv(_HERE / ".env")

from config.settings import settings
from database.db import get_engine, get_session, init_db
from utils.logging import get_logger, setup_logging


def _setup() -> None:
    """One-time setup: create directories, init DB, configure logging."""
    settings.ensure_dirs()
    setup_logging(
        level=settings.log_level,
        log_file=settings.log_file,
        console=True,
    )
    init_db()


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def cmd_import_kindergartens(args: argparse.Namespace) -> int:
    """
    Load official kindergarten data into the database.

    Sources (in priority order):
      1. --csv-file if provided
      2. MOE open data (if --try-moe-api)
      3. Built-in seed data (default)
    """
    _setup()
    logger = get_logger(__name__)
    from services.kindergarten_service import KindergartenService

    csv_file = Path(args.csv_file) if getattr(args, "csv_file", None) else None
    try_moe = getattr(args, "try_moe_api", False)

    engine = get_engine()

    from sqlalchemy.orm import Session
    session = Session(engine)
    try:
        svc = KindergartenService(session)
        result = svc.import_kindergartens(
            csv_file=csv_file,
            use_seed=True,
            try_moe_api=try_moe,
        )
        print(f"\n[OK] Import complete")
        print(f"  Total records loaded : {result['total']}")
        print(f"  Inserted (new)       : {result['inserted']}")
        print(f"  Updated (existing)   : {result['updated']}")
        print(f"\nNext step: python main.py validate-kindergartens")
        return 0
    finally:
        session.close()


def cmd_validate_kindergartens(args: argparse.Namespace) -> int:
    """
    Validate all imported kindergartens.

    Keeps only New Taipei City public kindergartens.
    Writes results to data/validation/.
    """
    _setup()
    logger = get_logger(__name__)
    from services.kindergarten_service import KindergartenService

    engine = get_engine()
    from sqlalchemy.orm import Session
    session = Session(engine)
    try:
        svc = KindergartenService(session)
        report = svc.validate_kindergartens(write_csv=True)
        report.print_summary()
        print(f"\n  Valid CSV   : {svc.validator.valid_csv_path}")
        print(f"  Rejected CSV: {svc.validator.rejected_csv_path}")
        return 0
    finally:
        session.close()


def cmd_status(args: argparse.Namespace) -> int:
    """
    Show pipeline status and statistics.
    """
    _setup()
    engine = get_engine()
    from sqlalchemy.orm import Session
    session = Session(engine)
    try:
        _print_status(session)
        return 0
    finally:
        session.close()


def _print_status(session) -> None:
    """Print full status report."""
    from database.db import get_db_size_bytes
    from database.models import GoogleMatchStatus, PlaceFetchStatus
    from database.repository import (
        KindergartenRepository,
        GooglePlaceRepository,
        ReviewRepository,
        RejectedKindergartenRepository,
    )

    kg_repo = KindergartenRepository(session)
    gp_repo = GooglePlaceRepository(session)
    rv_repo = ReviewRepository(session)
    rj_repo = RejectedKindergartenRepository(session)

    total = kg_repo.count_total()
    public = kg_repo.count_public()
    rejected_db = kg_repo.count_rejected()
    rj_total = rj_repo.count_total()
    matched = kg_repo.count_by_match_status(GoogleMatchStatus.AUTO_ACCEPTED)
    needs_review = kg_repo.count_by_match_status(GoogleMatchStatus.NEEDS_REVIEW)
    no_place = kg_repo.count_by_match_status(GoogleMatchStatus.NO_RESULT)
    unmatched = kg_repo.count_by_match_status(GoogleMatchStatus.UNMATCHED)
    places_fetched = gp_repo.count_fetched()
    reviews = rv_repo.count_total()
    star_counts = {i: rv_repo.count_by_rating(i) for i in range(1, 6)}
    last_sync = rv_repo.latest_sync_time()
    db_size = get_db_size_bytes()

    district_breakdown = kg_repo.district_breakdown()

    print("\n" + "=" * 60)
    print("  PIPELINE STATUS")
    print("=" * 60)
    print(f"  Total official kindergartens:  {total:>6}")
    print(f"  Public kindergartens:          {public:>6}")
    print(f"  Rejected (in KG table):        {rejected_db:>6}")
    print(f"  Rejected (validation log):     {rj_total:>6}")
    print(f"  Needs manual review:           {needs_review:>6}")
    print("-" * 60)
    print("  GOOGLE PLACE MATCHING:")
    print(f"    Auto-accepted (>=0.85):      {matched:>6}")
    print(f"    Needs review (0.70-0.85):    {needs_review:>6}")
    print(f"    No result found:             {no_place:>6}")
    print(f"    Not yet matched:             {unmatched:>6}")
    print(f"  Places fetched (metadata):     {places_fetched:>6}")
    print("-" * 60)
    print("  REVIEWS COLLECTED:")
    print(f"    Total:                       {reviews:>6}")
    print(f"    1-star  : {star_counts[1]:>6}")
    print(f"    2-star  : {star_counts[2]:>6}")
    print(f"    3-star  : {star_counts[3]:>6}")
    print(f"    4-star  : {star_counts[4]:>6}")
    print(f"    5-star  : {star_counts[5]:>6}")
    print(f"  Last sync:         {last_sync.strftime('%Y-%m-%d %H:%M') if last_sync else 'N/A':>15}")
    print(f"  Database size:     {_format_bytes(db_size):>15}")
    print("-" * 60)
    print("  BY DISTRICT (公立幼兒園):")
    ntc_districts = settings.NEW_TAIPEI_DISTRICTS
    for district in ntc_districts:
        count = district_breakdown.get(district, 0)
        if count > 0:
            bar = "|" * min(count, 30)
            print(f"    {district:<8} {count:>4}  {bar}")
    # Show unknown districts if any
    for district, count in district_breakdown.items():
        if district not in ntc_districts and count > 0:
            print(f"    {district:<8} {count:>4}  ← 確認行政區")
    print("=" * 60)


def _format_bytes(size: int) -> str:
    if size == 0:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def cmd_match_places(args: argparse.Namespace) -> int:
    """
    Match kindergartens to Google Places.
    Requires GOOGLE_MAPS_API_KEY in environment.
    (Implemented in STEP 6)
    """
    _setup()
    try:
        _key = settings.google_maps_api_key  # validate key exists
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    limit = getattr(args, "limit", None)
    delay = getattr(args, "delay", settings.google_request_delay)

    print(f"Starting Place matching (limit={limit}, delay={delay}s)")
    print("→ This command requires STEP 6 implementation (collectors/google_places.py)")
    print("→ Current STEP: 3-5 (import + validate)")
    return 0


def cmd_fetch_reviews(args: argparse.Namespace) -> int:
    """
    Fetch Google Place metadata and reviews.
    Requires GOOGLE_MAPS_API_KEY in environment.
    (Implemented in STEP 7)
    """
    _setup()
    try:
        _key = settings.google_maps_api_key
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    limit = getattr(args, "limit", None)
    district = getattr(args, "district", None)
    print(f"Fetching reviews (limit={limit}, district={district})")
    print("→ This command requires STEP 7 implementation (collectors/google_places.py)")
    return 0


def cmd_retry_failed(args: argparse.Namespace) -> int:
    """Retry failed place/review fetch operations. (STEP 7)"""
    _setup()
    print("→ retry-failed requires STEP 7 implementation")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export all data to CSV/JSON. (STEP 7)"""
    _setup()
    print("→ export requires STEP 7 implementation")
    return 0


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="New Taipei Public Kindergarten Google Review Intelligence Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py import-kindergartens
  python main.py import-kindergartens --csv-file data/public_kindergartens.csv
  python main.py validate-kindergartens
  python main.py status
  python main.py match-places --limit 10
  python main.py fetch-reviews --district 板橋區
""",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # import-kindergartens
    p_import = sub.add_parser("import-kindergartens", help="Load official kindergarten data")
    p_import.add_argument(
        "--csv-file", metavar="PATH",
        help="Path to a local government CSV file to import"
    )
    p_import.add_argument(
        "--try-moe-api", action="store_true",
        help="Attempt to download from MOE open data URL"
    )

    # validate-kindergartens
    p_val = sub.add_parser(
        "validate-kindergartens",
        help="Validate and filter to NTC public kindergartens only"
    )

    # status
    p_status = sub.add_parser("status", help="Show pipeline status and statistics")

    # match-places
    p_match = sub.add_parser("match-places", help="Match kindergartens to Google Places")
    p_match.add_argument("--limit", type=int, metavar="N", help="Process at most N kindergartens")
    p_match.add_argument("--delay", type=float, metavar="SECS",
                         help="Seconds between API requests (default: from settings)")

    # fetch-reviews
    p_fetch = sub.add_parser("fetch-reviews", help="Fetch Google Place metadata and reviews")
    p_fetch.add_argument("--limit", type=int, metavar="N")
    p_fetch.add_argument("--district", metavar="DISTRICT", help="Filter by district e.g. 板橋區")
    p_fetch.add_argument("--delay", type=float, metavar="SECS")

    # retry-failed
    sub.add_parser("retry-failed", help="Retry failed place/review fetch operations")

    # export
    sub.add_parser("export", help="Export data to CSV/JSON")

    return parser


_COMMAND_MAP = {
    "import-kindergartens": cmd_import_kindergartens,
    "validate-kindergartens": cmd_validate_kindergartens,
    "status": cmd_status,
    "match-places": cmd_match_places,
    "fetch-reviews": cmd_fetch_reviews,
    "retry-failed": cmd_retry_failed,
    "export": cmd_export,
}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    handler = _COMMAND_MAP.get(args.command)
    if not handler:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
