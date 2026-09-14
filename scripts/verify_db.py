#!/usr/bin/env python3
"""
Check that a Dalal Street AI database is sane before you build on it.

    python scripts/verify_db.py
    python scripts/verify_db.py --db App/database/sample.db --strict

What it checks:

  1. Schema      - all 16 expected tables present; anything extra reported.
  2. Row counts  - per table, flagging empty ones.
  3. Prices      - daily_ohlc date range, symbol count, and runs of missing
                   trading days.
  4. References  - symbols in daily_ohlc with no row in stocks_master.
  5. Resolver    - the seven-tier TickerResolver against a fixture set of
                   known renames, index aliases and fuzzy names.

Exit code is 0 when every hard check passes, 1 otherwise. `--strict` also
turns warnings into failures.

This script never writes to the database: it opens its own connection with
`mode=ro`. The resolver smoke test constructs a `TickerResolver`, which opens
its own read-write handle, but only ever issues SELECTs.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from App.config import config as _config

    DEFAULT_DB_PATH = Path(_config.DB_PATH)
except Exception as _config_error:  # pragma: no cover - configuration fallback
    print(f"[WARN] Could not import App.config ({_config_error}); using defaults.")
    DEFAULT_DB_PATH = PROJECT_ROOT / "App" / "database" / "stock_market_new.db"


#: Must stay in step with EXPECTED_TABLES in scripts/bootstrap_db.py.
EXPECTED_TABLES: frozenset = frozenset(
    {
        "annual_financials",
        "bhavcopy_history",
        "corporate_events",
        "daily_ohlc",
        "delisting_events",
        "download_log",
        "fii_dii_data",
        "fundamentals",
        "ipo_data",
        "market_etfs",
        "market_indices",
        "metadata",
        "name_change_events",
        "quarterly_results",
        "stocks_master",
        "symbol_change_events",
    }
)

#: Longest run of consecutive missing weekdays treated as normal. NSE closes
#: for clustered holidays (Diwali, Holi) and this script deliberately does not
#: model the holiday calendar.
GAP_WEEKDAY_THRESHOLD = 5

#: Tables that are legitimately empty in a working install.
OPTIONAL_TABLES: frozenset = frozenset({"download_log", "bhavcopy_history"})


class Fixture(NamedTuple):
    query: str
    expected: str
    covers: str
    #: Optional. When set, the fixture passes if the resolver returns ANYTHING
    #: other than this value. Used for tier-ordering guards, where the point is
    #: "a stock/ETF ticker wins over the index alias" and the exact winning
    #: ticker varies with how complete the database is.
    reject: Optional[str] = None


#: One fixture per tier of the cascade in README.md. Expectations are what a
#: correctly built warehouse returns today, so a change here means either the
#: data or the tier ordering moved.
RESOLVER_FIXTURES: Tuple[Fixture, ...] = (
    Fixture("RELIANCE", "RELIANCE", "tier 1  - direct hit on stocks_master"),
    Fixture("NIFTYBEES", "NIFTYBEES", "tier 1b - ETF direct"),
    Fixture("TATAMOTORS", "TMPV", "tier 2  - recursive symbol chase"),
    Fixture("Tata Consultancy Services", "TCS", "tier 2.5- fuzzy company name"),
    Fixture("NIFTY50", "NIFTY 50", "tier 2.6- index alias"),
    Fixture(
        "Orchid Chemicals & Pharmaceuticals Limited",
        "ORCHIDPHAR",
        "tier 3  - company name change",
    ),
    # Tier ordering guard. Stock fuzzy matching deliberately runs before index
    # alias resolution, so "BANKNIFTY" lands on a tradeable *ticker* (BANKNIFTY1,
    # EBANKNIFTY, ... - which one depends on what is listed in your database),
    # never on the NIFTY BANK index. If it ever returns the index, the tier
    # ordering changed - see README.
    Fixture(
        "BANKNIFTY",
        "(any ticker, not the index)",
        "tier ordering: stock fuzzy beats index alias",
        reject="NIFTY BANK",
    ),
)


class Report:
    """Accumulates check results and decides the exit code."""

    def __init__(self, strict: bool) -> None:
        self.strict = strict
        self.failures: List[str] = []
        self.warnings: List[str] = []
        self.skipped: List[str] = []

    def ok(self, message: str) -> None:
        print(f"  [OK]   {message}")

    def info(self, message: str) -> None:
        print(f"         {message}")

    def warn(self, message: str) -> None:
        print(f"  [WARN] {message}")
        self.warnings.append(message)

    def fail(self, message: str) -> None:
        print(f"  [FAIL] {message}")
        self.failures.append(message)

    def skip(self, message: str) -> None:
        print(f"  [SKIP] {message}")
        self.skipped.append(message)

    @property
    def exit_code(self) -> int:
        if self.failures:
            return 1
        if self.strict and self.warnings:
            return 1
        return 0


def _section(title: str) -> None:
    print()
    print(f"--- {title} " + "-" * max(0, 66 - len(title)))


def _human_bytes(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size):,} B"
        size /= 1024.0
    return f"{size:,.1f} TB"  # pragma: no cover - unreachable


def _tables(conn: sqlite3.Connection) -> Set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def check_schema(conn: sqlite3.Connection, report: Report) -> Set[str]:
    _section("SCHEMA")
    present = _tables(conn)
    missing = sorted(EXPECTED_TABLES - present)
    extra = sorted(present - EXPECTED_TABLES)

    if missing:
        report.fail(f"{len(missing)} expected table(s) missing: {', '.join(missing)}")
        report.info("Rebuild with: python scripts/bootstrap_db.py --schema-only")
    else:
        report.ok(f"all {len(EXPECTED_TABLES)} expected tables present")

    if extra:
        report.warn(f"{len(extra)} unexpected table(s): {', '.join(extra)}")
        report.info("Legacy tables from an older schema, or a table nobody documented.")
    return present


def check_row_counts(conn: sqlite3.Connection, tables: Set[str], report: Report) -> None:
    _section("ROW COUNTS")
    if not tables:
        report.fail("database has no tables at all")
        return

    names = sorted(tables)
    width = max(len(name) for name in names)
    empty: List[str] = []
    total = 0
    for name in names:
        try:
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.Error as exc:
            report.fail(f"{name}: unreadable ({exc})")
            continue
        total += count
        marker = "  <- empty" if count == 0 else ""
        print(f"         {name.ljust(width)}  {count:>12,}{marker}")
        if count == 0:
            empty.append(name)
    print(f"         {'TOTAL'.ljust(width)}  {total:>12,}")

    significant = [name for name in empty if name not in OPTIONAL_TABLES]
    if significant:
        report.warn(f"{len(significant)} table(s) empty: {', '.join(significant)}")
    else:
        report.ok("no unexpectedly empty tables")


def _parse_date(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _missing_weekdays(start: datetime, end: datetime) -> int:
    """Weekdays strictly between two dates."""
    count = 0
    cursor = start + timedelta(days=1)
    while cursor < end:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def check_daily_ohlc(conn: sqlite3.Connection, tables: Set[str], report: Report) -> None:
    _section("DAILY_OHLC COVERAGE")
    if "daily_ohlc" not in tables:
        report.fail("daily_ohlc is missing; skipping coverage checks")
        return

    rows = conn.execute("SELECT COUNT(*) FROM daily_ohlc").fetchone()[0]
    if rows == 0:
        report.warn("daily_ohlc is empty; no coverage to check")
        return

    low, high = conn.execute("SELECT MIN(date), MAX(date) FROM daily_ohlc").fetchone()
    symbols = conn.execute("SELECT COUNT(*) FROM (SELECT DISTINCT symbol FROM daily_ohlc)").fetchone()[0]
    report.ok(f"{rows:,} rows, {symbols:,} distinct symbols, {low} -> {high}")

    dates = [
        parsed
        for parsed in (
            _parse_date(row[0]) for row in conn.execute("SELECT DISTINCT date FROM daily_ohlc ORDER BY date")
        )
        if parsed is not None
    ]
    report.info(f"{len(dates):,} distinct trading days on file")

    gaps: List[Tuple[str, str, int]] = []
    for previous, current in zip(dates, dates[1:]):
        missing = _missing_weekdays(previous, current)
        if missing > GAP_WEEKDAY_THRESHOLD:
            gaps.append((previous.strftime("%Y-%m-%d"), current.strftime("%Y-%m-%d"), missing))

    report.info(
        f"gap threshold: more than {GAP_WEEKDAY_THRESHOLD} consecutive missing weekdays "
        "(weekends skipped; NSE holidays are NOT modelled, so clustered "
        "holidays can show up here legitimately)"
    )
    if gaps:
        report.warn(f"{len(gaps)} gap(s) longer than {GAP_WEEKDAY_THRESHOLD} weekdays")
        for start, end, missing in gaps[:20]:
            report.info(f"{start} -> {end}   {missing} weekdays missing")
        if len(gaps) > 20:
            report.info(f"... and {len(gaps) - 20} more")
    else:
        report.ok("no unexplained gaps in the trading calendar")


def check_referential_integrity(conn: sqlite3.Connection, tables: Set[str], report: Report) -> None:
    _section("REFERENTIAL INTEGRITY")
    if not {"daily_ohlc", "stocks_master"} <= tables:
        report.fail("daily_ohlc or stocks_master is missing; cannot check orphans")
        return

    # Both sides come off an index, so this stays cheap even on a 10M-row table.
    ohlc_symbols = {row[0] for row in conn.execute("SELECT DISTINCT symbol FROM daily_ohlc")}
    master_symbols = {row[0] for row in conn.execute("SELECT symbol FROM stocks_master")}
    orphans = {symbol for symbol in ohlc_symbols - master_symbols if symbol is not None}

    # `stocks_master` lists securities that trade TODAY. `daily_ohlc` holds 30
    # years of history, so a symbol being absent from master is usually correct:
    # the company was delisted, merged, renamed, or the symbol was a temporary
    # rights entitlement. Only symbols we cannot account for are a real problem.
    explained: Set[str] = set()

    for table, column in (
        ("delisting_events", "symbol"),
        ("symbol_change_events", "old_symbol"),
        ("name_change_events", "symbol"),
    ):
        if table in tables:
            try:
                explained |= {
                    row[0] for row in conn.execute(f'SELECT DISTINCT "{column}" FROM "{table}"')
                    if row[0]
                }
            except sqlite3.Error:
                pass

    # Rights entitlements / partly-paid series: -RE, -RE1, -PP, -BE, -BL, -N1...
    series_suffix = re.compile(r"-(RE\d*|PP\d*|BE|BL|BT|BZ|IL|IQ|N\d+|Y\d+|W\d+)$", re.I)

    unexplained = sorted(
        s for s in orphans
        if s not in explained and not series_suffix.search(s)
    )
    accounted = len(orphans) - len(unexplained)

    if not orphans:
        report.ok(f"every daily_ohlc symbol ({len(ohlc_symbols):,}) exists in stocks_master")
    elif not unexplained:
        report.ok(
            f"{len(orphans):,} historical symbol(s) absent from stocks_master, all accounted for "
            f"(delisted, renamed, or non-EQ series)"
        )
    else:
        # Still only a warning. stocks_master reflects the current listing universe;
        # an unmatched historical symbol means incomplete corporate-action data,
        # not a corrupt database.
        report.warn(
            f"{len(unexplained):,} historical symbol(s) in daily_ohlc are not explained by "
            f"delisting, rename or series suffix ({accounted:,} others are accounted for)"
        )
        report.info(", ".join(unexplained[:20]) + (" ..." if len(unexplained) > 20 else ""))
        report.info("These are most likely pre-1999 delistings that NSE no longer publishes.")
        report.info("To improve coverage, re-run: App/scriptsrebuild/04_daily_nse_update.py")

    unpriced = len(master_symbols - ohlc_symbols)
    if unpriced:
        report.info(f"{unpriced:,} stocks_master symbol(s) have no price history (normal for a sample)")


def check_resolver(db_path: Path, report: Report) -> None:
    _section("TICKER RESOLVER")
    try:
        from App.src.data_fetcher.ticker_resolver import TickerResolver
    except Exception as exc:
        report.skip(f"cannot import TickerResolver: {exc}")
        report.info("Install the runtime dependencies: pip install -r App/api/requirements.txt")
        return

    try:
        try:
            resolver = TickerResolver(str(db_path))
        except TypeError:
            resolver = TickerResolver()  # signature that defaults to config.DB_PATH
    except Exception as exc:
        report.fail(f"TickerResolver failed to initialise: {exc}")
        return

    width = max(len(fixture.query) for fixture in RESOLVER_FIXTURES)
    mismatches = 0
    for fixture in RESOLVER_FIXTURES:
        try:
            result = resolver.resolve_any(fixture.query)
        except Exception as exc:
            report.fail(f"resolver raised on {fixture.query!r}: {exc}")
            continue

        resolved = result.get("resolved_ticker") or result.get("resolved_index_name")
        method = result.get("resolution_method", "?")
        confidence = result.get("confidence", 0)
        got = str(resolved or "").strip().upper()
        if fixture.reject is not None:
            hit = bool(got) and got != fixture.reject.upper()
        else:
            hit = got == fixture.expected.upper()
        status = "ok " if hit else "??"
        print(
            f"  [{status}]  {fixture.query.ljust(width)}  ->  "
            f"{str(resolved or '(unresolved)'):<12}  {str(confidence):>3}  "
            f"{method:<24}  {fixture.covers}"
        )
        if not hit:
            mismatches += 1
            if fixture.reject is not None:
                report.info(f"        expected anything EXCEPT {fixture.reject}")
            else:
                report.info(f"        expected {fixture.expected}")

    if mismatches:
        report.warn(
            f"{mismatches} of {len(RESOLVER_FIXTURES)} fixtures did not resolve as expected "
            "(usual cause: the sample lacks the rows that tier needs)"
        )
    else:
        report.ok(f"all {len(RESOLVER_FIXTURES)} resolver fixtures resolved as expected")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify_db.py",
        description="Validate a Dalal Street AI SQLite database. Read-only.",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Database to check (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures.",
    )
    parser.add_argument(
        "--no-resolver",
        action="store_true",
        help="Skip the ticker resolver smoke test.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    db_path = args.db.expanduser()
    if not db_path.is_absolute():
        db_path = (PROJECT_ROOT / db_path).resolve()
    else:
        db_path = db_path.resolve()

    print("=" * 70)
    print("DALAL STREET AI - DATABASE VERIFICATION")
    print("=" * 70)
    print(f"  Database : {db_path}")

    if not db_path.exists():
        print(f"  Size     : -")
        print()
        print(f"  [FAIL] No database at {db_path}")
        print("         Build one: python scripts/bootstrap_db.py --sample")
        return 1
    print(f"  Size     : {_human_bytes(db_path.stat().st_size)}")
    print(f"  Mode     : read-only{'  (strict)' if args.strict else ''}")

    report = Report(strict=args.strict)
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print()
        print(f"  [FAIL] Could not open the database read-only: {exc}")
        return 1

    try:
        tables = check_schema(conn, report)
        check_row_counts(conn, tables, report)
        check_daily_ohlc(conn, tables, report)
        check_referential_integrity(conn, tables, report)
    finally:
        conn.close()

    if args.no_resolver:
        _section("TICKER RESOLVER")
        report.skip("--no-resolver")
    else:
        check_resolver(db_path, report)

    _section("SUMMARY")
    print(f"         failures : {len(report.failures)}")
    print(f"         warnings : {len(report.warnings)}")
    print(f"         skipped  : {len(report.skipped)}")
    code = report.exit_code
    print()
    if code == 0 and report.warnings:
        print(f"  [PASS] No hard failures, {len(report.warnings)} warning(s) above.")
    elif code == 0:
        print("  [PASS] Database looks usable.")
    elif report.failures:
        print("  [FAIL] Hard checks failed:")
        for failure in report.failures:
            print(f"         - {failure}")
    else:
        print("  [FAIL] Warnings promoted to failures by --strict:")
        for warning in report.warnings:
            print(f"         - {warning}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
