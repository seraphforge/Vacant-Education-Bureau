"""
main.py
-------
Main CLI entry point for New Taipei Public Kindergarten Intelligence V2.

Usage::

    python main.py --help
    python main.py init
    python main.py import-official
    python main.py validate
    python main.py data-quality
    python main.py status
    python main.py match-places --district 板橋區 --limit 10
    python main.py fetch-reviews --all
    python main.py build-master
    python main.py export
    python main.py export --district 板橋區
    python main.py retry-failed
    python main.py district-stats --district 板橋區
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

app = typer.Typer(
    name="ntpc-kindergarten",
    help="New Taipei Public Kindergarten Intelligence V2",
    add_completion=False,
)

console = Console()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent


def _setup() -> None:
    """Ensure logging is initialised before every command."""
    from utils.logging import setup_logging

    setup_logging()


def _abort(msg: str, code: int = 1) -> None:
    """Print an error panel and exit with *code*."""
    console.print(Panel(f"[bold red]ERROR:[/bold red] {msg}", expand=False))
    raise typer.Exit(code=code)


def _success(msg: str) -> None:
    console.print(f"[bold green]✓[/bold green] {msg}")


def _warn(msg: str) -> None:
    console.print(f"[bold yellow]⚠[/bold yellow]  {msg}")


def _info(msg: str) -> None:
    console.print(f"[cyan]→[/cyan] {msg}")


# ---------------------------------------------------------------------------
# Directory / database initialisation
# ---------------------------------------------------------------------------

_DIRS_TO_CREATE = [
    "data/db",
    "data/raw/official",
    "data/raw/google",
    "data/rejected",
    "exports/all",
    "logs",
]


def _ensure_directories() -> None:
    for rel in _DIRS_TO_CREATE:
        d = _PROJECT_ROOT / rel
        d.mkdir(parents=True, exist_ok=True)

    from config.new_taipei_districts import NEW_TAIPEI_DISTRICTS

    for district in NEW_TAIPEI_DISTRICTS:
        (_PROJECT_ROOT / "data" / "raw" / "google" / district).mkdir(
            parents=True, exist_ok=True
        )
        (_PROJECT_ROOT / "exports" / "by_district" / district).mkdir(
            parents=True, exist_ok=True
        )


# ---------------------------------------------------------------------------
# Command: init
# ---------------------------------------------------------------------------


@app.command("init")
def cmd_init() -> None:
    """Initialise all 4 databases and create the directory structure."""
    _setup()
    console.print(
        Panel(
            "[bold]Initialising New Taipei Kindergarten Intelligence V2[/bold]",
            expand=False,
        )
    )

    try:
        _info("Creating directory structure …")
        _ensure_directories()
        _success("Directories ready.")

        _info("Creating official_kindergartens.db …")
        import database.official_db as official_db

        official_db.create_tables()
        _success("official_kindergartens.db OK")

        _info("Creating google_places.db …")
        import database.places_db as places_db

        places_db.create_tables()
        _success("google_places.db OK")

        _info("Creating reviews.db …")
        import database.reviews_db as reviews_db

        reviews_db.create_tables()
        _success("reviews.db OK")

        _info("Creating kindergarten_reviews_master.db …")
        import database.master_db as master_db

        master_db.create_tables()
        _success("kindergarten_reviews_master.db OK")

        console.print()
        console.print(
            "[bold green]Initialisation complete.[/bold green] "
            "All 4 databases and directories are ready."
        )
    except Exception as exc:
        _abort(f"Initialisation failed: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Command: import-official
# ---------------------------------------------------------------------------


@app.command("import-official")
def cmd_import_official(
    force: bool = typer.Option(
        False, "--force", help="Re-import even if data already exists."
    ),
) -> None:
    """Fetch and import the official NTPC kindergarten dataset."""
    _setup()
    _info("Starting official kindergarten import …")

    try:
        from collectors.official_kindergartens import OfficialKindergartensCollector

        collector = OfficialKindergartensCollector()
        with console.status("[cyan]Fetching official data …[/cyan]"):
            result = collector.collect(force=force)

        console.print()
        console.print(
            f"[bold green]Import complete.[/bold green] "
            f"total={result.total}  success={result.success}  "
            f"failed={result.failed}  skipped={result.skipped}"
        )
    except Exception as exc:
        _abort(f"Import failed: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Command: validate
# ---------------------------------------------------------------------------


@app.command("validate")
def cmd_validate() -> None:
    """Validate the official dataset for completeness and correctness.

    Exits with code 1 if CRITICAL issues are found.
    """
    _setup()
    _info("Validating official dataset …")

    try:
        import database.official_db as official_db
        from config.new_taipei_districts import NEW_TAIPEI_DISTRICTS_SET

        rows = official_db.get_all_kindergartens()

        missing_district = [r for r in rows if not r.get("district")]
        unknown_district = [
            r
            for r in rows
            if r.get("district") and r["district"] not in NEW_TAIPEI_DISTRICTS_SET
        ]
        not_public = [r for r in rows if not r.get("is_public")]

        table = Table(title="Validation Results", show_header=True)
        table.add_column("Check", style="bold")
        table.add_column("Count", justify="right")
        table.add_column("Status")

        def _status(count: int, critical: bool = True) -> str:
            if count == 0:
                return "[bold green]PASS[/bold green]"
            return (
                "[bold red]FAIL (CRITICAL)[/bold red]"
                if critical
                else "[bold yellow]WARN[/bold yellow]"
            )

        table.add_row(
            "Total records", str(len(rows)), "[cyan]INFO[/cyan]"
        )
        table.add_row(
            "Records without district",
            str(len(missing_district)),
            _status(len(missing_district)),
        )
        table.add_row(
            "Records with UNKNOWN/invalid district",
            str(len(unknown_district)),
            _status(len(unknown_district)),
        )
        table.add_row(
            "Non-public records (is_public≠1)",
            str(len(not_public)),
            _status(len(not_public)),
        )

        console.print()
        console.print(table)

        critical_issues = len(missing_district) + len(unknown_district) + len(not_public)
        if critical_issues:
            console.print()
            _warn(
                f"{critical_issues} CRITICAL issue(s) found. "
                "Run [bold]data-quality[/bold] for full details."
            )
            raise typer.Exit(code=1)
        else:
            console.print()
            _success("Dataset passed all validation checks.")
    except typer.Exit:
        raise
    except Exception as exc:
        _abort(f"Validation failed: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Command: data-quality
# ---------------------------------------------------------------------------


@app.command("data-quality")
def cmd_data_quality(
    district: Optional[str] = typer.Option(
        None, "--district", help="Restrict checks to a single district."
    ),
) -> None:
    """Run the full data-quality check suite and print a coloured report.

    Exits with code 1 if CRITICAL severity issues are found.
    """
    _setup()
    _info(
        f"Running data-quality checks"
        + (f" for district '{district}'" if district else "")
        + " …"
    )

    try:
        from analysis.data_quality import DataQualityChecker

        checker = DataQualityChecker()

        with console.status("[cyan]Analysing data …[/cyan]"):
            issues = checker.check_all(district=district)

        checker.print_report(issues)

        critical = [i for i in issues if i.severity == "CRITICAL"]
        if critical:
            console.print()
            _warn(f"{len(critical)} CRITICAL issue(s) found.")
            raise typer.Exit(code=1)
        else:
            console.print()
            _success(f"Data-quality check complete. {len(issues)} issue(s) found.")
    except typer.Exit:
        raise
    except Exception as exc:
        _abort(f"data-quality failed: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Command: status
# ---------------------------------------------------------------------------


@app.command("status")
def cmd_status(
    district: Optional[str] = typer.Option(
        None, "--district", help="Show stats for a single district only."
    ),
) -> None:
    """Print a live status dashboard for the whole project."""
    _setup()

    try:
        import database.master_db as master_db
        from sqlalchemy import func, select, text

        with master_db.get_connection() as conn:
            # ----- counts ------------------------------------------------
            kg_total = (
                conn.execute(
                    select(func.count()).select_from(master_db.kindergartens_table)
                ).scalar()
                or 0
            )

            auto_accept = (
                conn.execute(
                    select(func.count())
                    .select_from(master_db.places_table)
                    .where(master_db.places_table.c.match_status == "AUTO_ACCEPT")
                ).scalar()
                or 0
            )
            manual_review = (
                conn.execute(
                    select(func.count())
                    .select_from(master_db.places_table)
                    .where(
                        master_db.places_table.c.match_status == "MANUAL_REVIEW"
                    )
                ).scalar()
                or 0
            )
            no_match = (
                conn.execute(
                    select(func.count())
                    .select_from(master_db.places_table)
                    .where(master_db.places_table.c.match_status == "NO_MATCH")
                ).scalar()
                or 0
            )

            review_collected = (
                conn.execute(
                    select(func.count()).select_from(master_db.reviews_table)
                ).scalar()
                or 0
            )
            google_total_row = conn.execute(
                select(
                    func.sum(master_db.places_table.c.google_user_rating_count)
                )
            ).scalar()
            google_total = int(google_total_row) if google_total_row else 0

            last_sync_row = conn.execute(
                select(master_db.district_stats_table.c.last_sync_at)
                .order_by(master_db.district_stats_table.c.last_sync_at.desc())
                .limit(1)
            ).scalar()
            last_sync = last_sync_row or "—"

        # ----- header ----------------------------------------------------
        console.print()
        console.rule(
            "[bold]New Taipei Kindergarten Intelligence V2[/bold]", style="cyan"
        )
        console.print()

        console.print("[bold]Official dataset:[/bold]")
        console.print(f"  Public kindergartens: [cyan]{kg_total}[/cyan]")
        console.print()

        console.print("[bold]Google Places:[/bold]")
        console.print(f"  Matched (AUTO_ACCEPT): [green]{auto_accept}[/green]")
        console.print(f"  Manual review:         [yellow]{manual_review}[/yellow]")
        console.print(f"  Not found:             [red]{no_match}[/red]")
        console.print()

        console.print("[bold]Reviews:[/bold]")
        console.print(f"  Collected:                         [cyan]{review_collected}[/cyan]")
        console.print(
            f"  Google total (from place metadata): [cyan]{google_total}[/cyan]"
        )
        console.print()

        console.print(f"[bold]Last sync:[/bold] [dim]{last_sync}[/dim]")
        console.print()

        # ----- districts table -------------------------------------------
        stats = master_db.get_district_stats(district=district)

        dist_table = Table(
            title="Districts" + (f" — {district}" if district else ""),
            show_header=True,
        )
        dist_table.add_column("District", style="bold")
        dist_table.add_column("Public", justify="right")
        dist_table.add_column("Matched", justify="right")
        dist_table.add_column("Reviews", justify="right")
        dist_table.add_column("Avg Rating", justify="right")

        # If district filter is applied and master has no stats yet, fall back
        # to counting from raw tables
        if not stats and district:
            with master_db.get_connection() as conn:
                kg_d = (
                    conn.execute(
                        select(func.count())
                        .select_from(master_db.kindergartens_table)
                        .where(
                            master_db.kindergartens_table.c.district == district
                        )
                    ).scalar()
                    or 0
                )
                pl_d = (
                    conn.execute(
                        select(func.count())
                        .select_from(master_db.places_table)
                        .where(
                            master_db.places_table.c.district == district,
                            master_db.places_table.c.match_status == "AUTO_ACCEPT",
                        )
                    ).scalar()
                    or 0
                )
                rv_d = (
                    conn.execute(
                        select(func.count())
                        .select_from(master_db.reviews_table)
                        .where(master_db.reviews_table.c.district == district)
                    ).scalar()
                    or 0
                )
                avg_row = conn.execute(
                    select(func.avg(master_db.reviews_table.c.rating)).where(
                        master_db.reviews_table.c.district == district
                    )
                ).scalar()
            avg_str = f"{avg_row:.2f}" if avg_row else "—"
            dist_table.add_row(district, str(kg_d), str(pl_d), str(rv_d), avg_str)
        else:
            for s in stats:
                avg_g = s.get("avg_google_rating")
                avg_str = f"{avg_g:.2f}" if avg_g else "—"
                dist_table.add_row(
                    s["district"],
                    str(s.get("public_kindergarten_count", 0)),
                    str(s.get("matched_place_count", 0)),
                    str(s.get("collected_review_count", 0)),
                    avg_str,
                )

        console.print(dist_table)

    except Exception as exc:
        _abort(f"status failed: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Command: match-places
# ---------------------------------------------------------------------------


@app.command("match-places")
def cmd_match_places(
    district: Optional[str] = typer.Option(
        None, "--district", help="Match only this district."
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Maximum number of kindergartens to process."
    ),
    all_districts: bool = typer.Option(
        False, "--all", help="Process all 29 districts."
    ),
    delay: float = typer.Option(
        0.7, "--delay", help="Seconds to wait between API requests."
    ),
) -> None:
    """Run the Google Places collector to match kindergartens."""
    _setup()

    if not district and not all_districts:
        _abort(
            "Specify --district DISTRICT or --all to process all districts."
        )

    try:
        from collectors.google_places import GooglePlacesCollector
        from config.new_taipei_districts import NEW_TAIPEI_DISTRICTS

        districts = NEW_TAIPEI_DISTRICTS if all_districts else [district]

        for d in districts:
            _info(f"Matching places for district: {d} …")
            try:
                collector = GooglePlacesCollector(delay=delay)
                with console.status(f"[cyan]Querying Google Places for {d} …[/cyan]"):
                    result = collector.collect(district=d, limit=limit)
                _success(
                    f"{d}: total={result.total}  success={result.success}  "
                    f"failed={result.failed}  skipped={result.skipped}"
                )
            except Exception as exc:
                _warn(f"match-places for '{d}' failed: {exc}")
    except Exception as exc:
        _abort(f"match-places failed: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Command: fetch-reviews
# ---------------------------------------------------------------------------


@app.command("fetch-reviews")
def cmd_fetch_reviews(
    district: Optional[str] = typer.Option(
        None, "--district", help="Fetch reviews only for this district."
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Maximum number of places to process."
    ),
    all_districts: bool = typer.Option(
        False, "--all", help="Process all 29 districts."
    ),
    delay: float = typer.Option(
        0.7, "--delay", help="Seconds to wait between API requests."
    ),
) -> None:
    """Fetch Google reviews for matched kindergartens."""
    _setup()

    if not district and not all_districts:
        _abort(
            "Specify --district DISTRICT or --all to process all districts."
        )

    try:
        from collectors.google_reviews import GoogleReviewsCollector
        from config.new_taipei_districts import NEW_TAIPEI_DISTRICTS

        districts = NEW_TAIPEI_DISTRICTS if all_districts else [district]

        for d in districts:
            _info(f"Fetching reviews for district: {d} …")
            try:
                collector = GoogleReviewsCollector(delay=delay)
                with console.status(
                    f"[cyan]Fetching reviews for {d} …[/cyan]"
                ):
                    result = collector.collect(district=d, limit=limit)
                _success(
                    f"{d}: total={result.total}  success={result.success}  "
                    f"failed={result.failed}  skipped={result.skipped}"
                )
            except Exception as exc:
                _warn(f"fetch-reviews for '{d}' failed: {exc}")
    except Exception as exc:
        _abort(f"fetch-reviews failed: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Command: build-master
# ---------------------------------------------------------------------------


@app.command("build-master")
def cmd_build_master() -> None:
    """Integrate all source databases into the master DB and compute stats."""
    _setup()
    _info("Building master database …")

    try:
        from services.master_builder import MasterBuilder

        builder = MasterBuilder()
        with console.status("[cyan]Building master database …[/cyan]"):
            summary = builder.build()

        console.print()

        tbl = Table(title="Master Build Summary", show_header=True)
        tbl.add_column("Metric")
        tbl.add_column("Value", justify="right")

        tbl.add_row(
            "Kindergartens synced", str(summary.get("kindergartens", 0))
        )
        tbl.add_row("Places synced", str(summary.get("places", 0)))
        tbl.add_row("Reviews synced", str(summary.get("reviews", 0)))
        tbl.add_row("FK errors", str(summary.get("fk_errors", 0)))
        tbl.add_row("Build time", summary.get("build_time", "—"))

        console.print(tbl)
        _success("Master database built successfully.")
    except Exception as exc:
        _abort(f"build-master failed: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Command: export
# ---------------------------------------------------------------------------


@app.command("export")
def cmd_export(
    district: Optional[str] = typer.Option(
        None, "--district", help="Export only this district."
    ),
) -> None:
    """Export data to CSV/JSON/JSONL files."""
    _setup()

    try:
        from services.export_service import ExportService

        svc = ExportService()

        if district:
            _info(f"Exporting district: {district} …")
            with console.status(f"[cyan]Exporting {district} …[/cyan]"):
                svc.export_district(district)
            _success(
                f"Export complete for '{district}'. "
                f"Files in: exports/by_district/{district}/"
            )
        else:
            _info("Exporting all data …")
            with console.status("[cyan]Exporting all data …[/cyan]"):
                summary = svc.export_all()

            console.print()
            tbl = Table(title="Export Summary", show_header=True)
            tbl.add_column("File")
            tbl.add_column("Path", overflow="fold")

            for key, val in summary.items():
                if key not in ("districts_exported", "export_time"):
                    tbl.add_row(key, str(val))

            tbl.add_row(
                "districts_exported",
                str(summary.get("districts_exported", 0)),
            )
            console.print(tbl)
            _success("Export complete.")
    except Exception as exc:
        _abort(f"export failed: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Command: retry-failed
# ---------------------------------------------------------------------------


@app.command("retry-failed")
def cmd_retry_failed(
    district: Optional[str] = typer.Option(
        None, "--district", help="Retry only failed runs for this district."
    ),
) -> None:
    """Retry all FAILED crawl_runs."""
    _setup()
    _info("Looking for FAILED crawl runs …")

    try:
        import database.master_db as master_db
        from sqlalchemy import select

        with master_db.get_connection() as conn:
            stmt = (
                select(master_db.crawl_runs_table)
                .where(master_db.crawl_runs_table.c.status == "FAILED")
                .order_by(master_db.crawl_runs_table.c.started_at)
            )
            if district:
                stmt = stmt.where(
                    master_db.crawl_runs_table.c.district == district
                )
            failed_runs = conn.execute(stmt).mappings().all()

        if not failed_runs:
            _success("No FAILED crawl runs found.")
            return

        console.print(
            f"Found [yellow]{len(failed_runs)}[/yellow] FAILED run(s)."
        )

        retried = 0
        for run in failed_runs:
            run = dict(run)
            crawler = run.get("crawler", "")
            run_district = run.get("district", "")

            _info(
                f"Retrying: crawler={crawler!r}  district={run_district!r}  "
                f"started_at={run.get('started_at')}"
            )

            try:
                if crawler == "official_kindergartens":
                    from collectors.official_kindergartens import (
                        OfficialKindergartensCollector,
                    )

                    collector = OfficialKindergartensCollector()
                    result = collector.collect(force=True)

                elif crawler == "google_places":
                    from collectors.google_places import GooglePlacesCollector

                    collector = GooglePlacesCollector()
                    result = collector.collect(district=run_district)

                elif crawler == "google_reviews":
                    from collectors.google_reviews import GoogleReviewsCollector

                    collector = GoogleReviewsCollector()
                    result = collector.collect(district=run_district, force=True)

                else:
                    _warn(f"Unknown crawler type: {crawler!r} — skipping.")
                    continue

                _success(
                    f"Retried {crawler} ({run_district}): "
                    f"total={result.total}  success={result.success}  "
                    f"failed={result.failed}"
                )
                retried += 1

            except Exception as exc:
                _warn(
                    f"Retry failed for run id={run.get('id')}: {exc}"
                )

        console.print()
        _success(f"Retry complete. {retried}/{len(failed_runs)} runs retried.")
    except Exception as exc:
        _abort(f"retry-failed failed: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Command: district-stats
# ---------------------------------------------------------------------------


@app.command("district-stats")
def cmd_district_stats(
    district: str = typer.Option(
        ..., "--district", help="District to show detailed stats for."
    ),
) -> None:
    """Show detailed statistics for a specific district."""
    _setup()

    try:
        import database.master_db as master_db
        from sqlalchemy import func, select

        # ---- district_stats row -----------------------------------------
        stats_rows = master_db.get_district_stats(
            district=district
        )
        if not stats_rows:
            _warn(
                f"No stats found for district '{district}'. "
                "Run [bold]build-master[/bold] first."
            )
        else:
            s = stats_rows[0]
            tbl = Table(
                title=f"District Stats — {district}", show_header=True
            )
            tbl.add_column("Metric", style="bold")
            tbl.add_column("Value", justify="right")

            tbl.add_row(
                "Public kindergartens",
                str(s.get("public_kindergarten_count", 0)),
            )
            tbl.add_row(
                "Matched places (AUTO_ACCEPT)",
                str(s.get("matched_place_count", 0)),
            )
            tbl.add_row(
                "Unmatched places",
                str(s.get("unmatched_place_count", 0)),
            )
            tbl.add_row(
                "Google review count (metadata)",
                str(s.get("total_google_review_count", 0)),
            )
            tbl.add_row(
                "Collected reviews",
                str(s.get("collected_review_count", 0)),
            )
            avg_g = s.get("avg_google_rating")
            tbl.add_row(
                "Avg Google rating",
                f"{avg_g:.2f}" if avg_g else "—",
            )
            avg_c = s.get("avg_collected_rating")
            tbl.add_row(
                "Avg collected rating",
                f"{avg_c:.2f}" if avg_c else "—",
            )
            for star in range(1, 6):
                tbl.add_row(
                    f"★{star} reviews",
                    str(s.get(f"rating_{star}_count", 0)),
                )
            tbl.add_row("Last sync", s.get("last_sync_at") or "—")
            console.print(tbl)

        # ---- kindergarten_stats rows ------------------------------------
        kg_stats = master_db.get_kindergarten_stats(district=district)
        if kg_stats:
            console.print()
            kg_tbl = Table(
                title=f"Kindergarten Stats — {district}",
                show_header=True,
            )
            kg_tbl.add_column("Name", overflow="fold")
            kg_tbl.add_column("Google ★", justify="right")
            kg_tbl.add_column("G.Reviews", justify="right")
            kg_tbl.add_column("Collected", justify="right")
            kg_tbl.add_column("Avg Rating", justify="right")
            kg_tbl.add_column("Latest Review")

            for ks in kg_stats:
                avg = ks.get("avg_collected_rating")
                avg_str = f"{avg:.2f}" if avg else "—"
                g_rating = ks.get("google_rating")
                g_str = f"{g_rating:.1f}" if g_rating else "—"
                kg_tbl.add_row(
                    ks.get("official_name") or "—",
                    g_str,
                    str(ks.get("google_review_count") or 0),
                    str(ks.get("collected_review_count") or 0),
                    avg_str,
                    ks.get("latest_review_time") or "—",
                )
            console.print(kg_tbl)

    except Exception as exc:
        _abort(f"district-stats failed: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
