from __future__ import annotations

import argparse
import re
import signal
from datetime import datetime
from pathlib import Path

from mira_etl.config import SourceConfig
from mira_etl.db import Database
from mira_etl.pipeline import run_pipeline


def stop_requested(signum: int, _frame: object) -> None:
    # Permite que run_pipeline registre ERROR al detener el contenedor con SIGTERM.
    raise SystemExit(128 + signum)


def main() -> None:
    signal.signal(signal.SIGTERM, stop_requested)
    parser = argparse.ArgumentParser(prog="mira-etl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run an ETL source for a period.")
    run.add_argument(
        "--source",
        required=True,
        help="Source configuration name.",
    )
    run.add_argument(
        "--period",
        default=None,
        help="Period as YYYYMM or an inclusive range such as '202501 - 202512'.",
    )
    run.add_argument("--local-zip", type=Path, default=None)
    run.add_argument("--work-dir", type=Path, default=Path("data/work"))
    run.add_argument("--config-dir", type=Path, default=Path("config/sources"))
    run.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of records fetched/loaded (quick smoke tests against a real database).",
    )
    run.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Run even when this source and period already have a SUCCESS audit row.",
    )
    run.add_argument(
        "--award-detail-limit",
        type=int,
        default=None,
        help=(
            "Nicaragua only: cap how many awarded processes get their supplier/amount "
            "detail fetched in this run (3 requests each). Defaults to a bounded batch "
            "so a routine run does not take hours; the corpus builds up across runs."
        ),
    )

    subparsers.add_parser("init-db", help="Create database schemas and tables.")
    subparsers.add_parser("check-db", help="Validate connectivity and schema without modifying it.")
    subparsers.add_parser("sources", help="List configured sources without connecting to the database.")
    history = subparsers.add_parser("history", help="Show recent database audit records.")
    history.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()

    if args.command == "sources":
        for config in SourceConfig.discover(Path("config/sources")):
            print(f"{config.source}\t{config.country_code}\t{config.download['type']}")
        return

    if args.command == "check-db":
        with Database.from_env() as db:
            db.validate_schema()
            row = db.fetch_one("select current_database() as db, current_user as role", ())
            print(f"Database schema OK: database={row['db']}, role={row['role']}")
        return

    if args.command == "history":
        if not 1 <= args.limit <= 1000:
            parser.error("--limit must be between 1 and 1000")
        with Database.from_env() as db:
            rows = db.fetch_all(
                "select id, source, period, status, started_at, finished_at "
                "from audit.etl_runs order by id desc limit %s", (args.limit,),
            )
        print("ID\tSOURCE\tPERIOD\tSTATUS\tSTARTED_AT\tFINISHED_AT")
        for row in rows:
            print("\t".join(str(row[key]) for key in (
                "id", "source", "period", "status", "started_at", "finished_at",
            )))
        return

    if args.command == "init-db":
        with Database.from_env() as db:
            for sql_file in sorted(Path("sql").glob("*.sql")):
                db.execute_sql_file(sql_file)
            db.validate_schema()
        print("Database schema initialized and validated.")
        return

    if args.command == "run":
        periods = resolve_periods(
            source=args.source,
            period=args.period,
            config_dir=args.config_dir,
        )
        if args.local_zip is not None and len(periods) > 1:
            raise SystemExit("--local-zip cannot be used with a period range.")
        processed = 0
        skipped = 0
        failed = 0
        is_backfill = len(periods) > 1
        for index, period in enumerate(periods, start=1):
            if is_backfill:
                print(f"Running period {period} ({index}/{len(periods)})...")
            try:
                result = run_pipeline(
                    source=args.source,
                    period=period,
                    config_dir=args.config_dir,
                    work_dir=args.work_dir,
                    local_zip=args.local_zip,
                    limit=args.limit,
                    force_reprocess=args.force_reprocess,
                    award_detail_limit=args.award_detail_limit,
                )
            except Exception as exc:
                if not is_backfill:
                    raise
                failed += 1
                print(f"[{args.source}] {period} - ERROR: {exc}")
                continue

            if result == "SKIPPED":
                skipped += 1
                print(f"[{args.source}] {period} - SKIPPED (already processed)")
            else:
                processed += 1
                print(f"[{args.source}] {period} - SUCCESS")

        if is_backfill:
            print(
                "\nBackfill completed\n\n"
                f"Source: {args.source}\n"
                f"Range: {periods[0]} - {periods[-1]}\n\n"
                f"Processed: {processed}\n"
                f"Skipped: {skipped}\n"
                f"Failed: {failed}"
            )
            if failed:
                raise SystemExit(1)


def resolve_period(*, source: str, period: str | None, config_dir: Path) -> str:
    """Resolve one period, retained for callers that require an individual run."""
    periods = resolve_periods(source=source, period=period, config_dir=config_dir)
    if len(periods) != 1:
        raise ValueError("Expected one period, received a range.")
    return periods[0]


def resolve_periods(*, source: str, period: str | None, config_dir: Path) -> list[str]:
    if period:
        values = parse_period_expression(period)
        config = SourceConfig.load(config_dir, source)
        if len(values) > 1 and config.download.get("type") == "html_session_scrape":
            raise SystemExit(
                "Period ranges are not supported for current-state HTML sources."
            )
        return values

    config = SourceConfig.load(config_dir, source)
    if config.download.get("type") == "html_session_scrape":
        return [datetime.now().strftime("%Y%m")]

    raise SystemExit("--period is required for historical ZIP/JSON sources.")


def parse_period_expression(value: str) -> list[str]:
    match = re.fullmatch(r"\s*(\d{6})(?:\s*-\s*(\d{6}))?\s*", value)
    if match is None:
        raise SystemExit("Period must use YYYYMM or 'YYYYMM - YYYYMM' format.")

    start = validate_period(match.group(1))
    end = validate_period(match.group(2) or match.group(1))
    if start > end:
        raise SystemExit("Period range start must not be after its end.")

    periods: list[str] = []
    year, month = divmod(start, 100)
    end_year, end_month = divmod(end, 100)
    while (year, month) <= (end_year, end_month):
        periods.append(f"{year:04d}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return periods


def validate_period(value: str) -> int:
    year = int(value[:4])
    month = int(value[4:])
    if year < 1 or month < 1 or month > 12:
        raise SystemExit(f"Invalid period '{value}'. Expected a valid YYYYMM value.")
    return year * 100 + month


if __name__ == "__main__":
    main()
