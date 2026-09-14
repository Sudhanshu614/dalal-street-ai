#!/usr/bin/env python3
"""
Build a SQLite warehouse for Dalal Street AI.

The repository ships no database. This script creates one, in one of three
shapes:

    python scripts/bootstrap_db.py --schema-only
        Empty database, every table, every index. Seconds.

    python scripts/bootstrap_db.py --sample
        Schema plus a small, genuinely usable slice of data: enough rows for
        the seven-tier ticker resolver to exercise every tier. Samples from a
        full database if one already exists at DB_PATH, otherwise pulls a
        minimal live slice from NSE.

    python scripts/bootstrap_db.py --from-existing <path>
        Same sample, extracted from the full database at <path>.

The schema is not written out by hand here. It is taken from the ingestion
scripts that build the real warehouse, so there is exactly one source of
truth:

    App/scriptsrebuild/01_create_schema.py          <- executed
    App/scriptsrebuild/02_create_new_tables.py      <- DDL harvested
    App/scriptsrebuild/11_import_ipo_data.py        <- DDL harvested
    App/scriptsrebuild/14_scrape_fii_dii_data.py    <- DDL harvested
    App/scripts/rebuild/10_daily_update_indices_etfs.py
    App/src/data_fetcher/bhavcopy_downloader.py
    App/scriptsrebuild/07_migrate_add_enhanced_fields.py

See docs/REBUILD.md for the full warehouse build, and docs/DATA.md for the
licensing constraints on anything you ingest.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Dict, List, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------
# Project root on sys.path so `App.*` imports work when this file is run
# directly as `python scripts/bootstrap_db.py` from the repository root.
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from App.config import config as _config

    DEFAULT_DB_PATH = Path(_config.DB_PATH)
    DEFAULT_CACHE_DIR = Path(_config.CACHE_DIR)
except Exception as _config_error:  # pragma: no cover - configuration fallback
    print(f"[WARN] Could not import App.config ({_config_error}); using defaults.")
    DEFAULT_DB_PATH = PROJECT_ROOT / "App" / "database" / "stock_market_new.db"
    DEFAULT_CACHE_DIR = PROJECT_ROOT / "cache"


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------

#: The tables a complete warehouse has. README.md documents 16 of them; this
#: set is the contract every mode of this script is checked against. If the
#: warehouse legitimately gains a table, add it here *and* to README.md.
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

#: Executed (not copy-pasted) to create the tables it owns.
CANONICAL_SCHEMA_SCRIPT = Path("App/scriptsrebuild/01_create_schema.py")

#: Scanned for CREATE TABLE / CREATE INDEX literals, in order. Later scripts
#: fill in the tables the canonical script predates.
SUPPLEMENTARY_DDL_SCRIPTS: Tuple[Path, ...] = (
    Path("App/scriptsrebuild/02_create_new_tables.py"),
    Path("App/scriptsrebuild/11_import_ipo_data.py"),
    Path("App/scriptsrebuild/14_scrape_fii_dii_data.py"),
    Path("App/src/data_fetcher/bhavcopy_downloader.py"),
    Path("App/scripts/rebuild/10_daily_update_indices_etfs.py"),
    Path("App/scripts/rebuild/12_split_indices_and_etfs.py"),
)

#: Tables whose supplementary definition supersedes the canonical one. The
#: canonical script predates the indices/ETF rework and still declares the
#: old, narrower shape.
OVERRIDE_TABLES: frozenset = frozenset({"market_indices", "market_etfs"})

#: Columns added to `fundamentals` after the canonical schema was written.
FUNDAMENTALS_MIGRATION_SCRIPT = Path("App/scriptsrebuild/07_migrate_add_enhanced_fields.py")

#: Liquid, widely-recognised NSE symbols. Used to pick the OHLC slice so the
#: sample is something a human can actually ask questions about.
WELL_KNOWN_SYMBOLS: Tuple[str, ...] = (
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "SBIN",
    "BHARTIARTL", "LT", "KOTAKBANK", "HINDUNILVR", "AXISBANK", "BAJFINANCE",
    "MARUTI", "ASIANPAINT", "TITAN", "SUNPHARMA", "TATAMOTORS", "TATASTEEL",
    "WIPRO", "ULTRACEMCO", "NESTLEIND", "POWERGRID", "NTPC", "ONGC",
    "HCLTECH", "JSWSTEEL", "ADANIENT", "ADANIPORTS", "COALINDIA", "GRASIM",
    "TECHM", "BAJAJFINSV", "DRREDDY", "CIPLA", "BRITANNIA", "EICHERMOT",
    "HEROMOTOCO", "INDUSINDBK", "APOLLOHOSP", "BPCL", "HINDALCO",
    "TATACONSUM", "SBILIFE", "HDFCLIFE", "M&M", "SHRIRAMFIN", "JIOFIN",
    "BAJAJ-AUTO", "ETERNAL", "TRENT", "DMART", "PIDILITIND", "VEDL",
)

#: Index names sampled into `market_indices`. The resolver's index-alias tier
#: (BANKNIFTY -> NIFTY BANK) builds its alias map from whatever is in here.
SAMPLE_INDEX_NAMES: Tuple[str, ...] = (
    "NIFTY 50", "NIFTY BANK", "NIFTY NEXT 50", "NIFTY 100", "NIFTY 500",
    "NIFTY IT", "NIFTY AUTO", "NIFTY FMCG", "NIFTY PHARMA", "NIFTY METAL",
    "NIFTY ENERGY", "NIFTY REALTY", "NIFTY PSU BANK",
    "NIFTY FINANCIAL SERVICES", "NIFTY MIDCAP 100", "NIFTY SMALLCAP 100",
    "INDIA VIX",
)

#: ETF symbols sampled into `market_etfs`. Stored with and without the -EQ
#: suffix because the table carries both forms and the resolver strips it.
SAMPLE_ETF_NAMES: Tuple[str, ...] = (
    "NIFTYBEES-EQ", "NIFTYBEES", "BANKBEES-EQ", "BANKBEES",
    "GOLDBEES-EQ", "GOLDBEES", "JUNIORBEES-EQ", "JUNIORBEES",
    "ITBEES-EQ", "ITBEES", "LIQUIDBEES-EQ", "LIQUIDBEES",
    "SILVERBEES-EQ", "SILVERBEES", "PSUBNKBEES-EQ", "PSUBNKBEES",
)

#: SQLite refuses more than 999 bound parameters by default.
_IN_CHUNK = 400

_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(?P<name>\w+)", re.IGNORECASE
)
_CREATE_INDEX_RE = re.compile(
    r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(?P<name>\w+)[\"'`\]]?"
    r"\s+ON\s+[\"'`\[]?(?P<table>\w+)",
    re.IGNORECASE,
)
_IDENTIFIER_RE = re.compile(r"^\w+$")
_COLUMN_TYPES = {"TEXT", "REAL", "INTEGER", "NUMERIC", "BLOB", "BOOLEAN"}


class BootstrapError(RuntimeError):
    """Anything that should stop the build with a readable message."""


class SampleDataUnavailable(BootstrapError):
    """No source of sample data: no local database, and no working network."""


# --------------------------------------------------------------------------
# Small output helpers
# --------------------------------------------------------------------------

_VERBOSE = False


def _vprint(message: str) -> None:
    if _VERBOSE:
        print(message)


def _section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def _human_bytes(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size):,} B"
        size /= 1024.0
    return f"{size:,.1f} TB"  # pragma: no cover - unreachable


def _db_file_size(db_path: Path) -> int:
    """Size of the database plus its write-ahead log siblings, if any."""
    total = db_path.stat().st_size if db_path.exists() else 0
    for suffix in ("-wal", "-shm"):
        sibling = db_path.with_name(db_path.name + suffix)
        if sibling.exists():
            total += sibling.stat().st_size
    return total


# --------------------------------------------------------------------------
# Schema: harvested from the ingestion scripts, never re-typed here
# --------------------------------------------------------------------------


def _load_module(path: Path, module_name: str) -> ModuleType:
    """Import a script by path. Needed because `01_create_schema.py` starts
    with a digit and therefore is not importable under its own name."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise BootstrapError(f"Could not load {path} as a module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _harvest_ddl(path: Path) -> List[str]:
    """Pull every CREATE TABLE / CREATE INDEX string literal out of a script.

    Parsing rather than executing means we pick up the DDL of scripts that
    would otherwise hit the network or prompt for input on import.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError as exc:
        raise BootstrapError(f"Could not parse {path}: {exc}") from exc

    statements: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if _CREATE_TABLE_RE.match(text) or _CREATE_INDEX_RE.match(text):
                statements.append(text)
    return statements


def _table_of(sql: str) -> Optional[str]:
    match = _CREATE_TABLE_RE.match(sql)
    return match.group("name") if match else None


def _index_target(sql: str) -> Optional[Tuple[str, str]]:
    match = _CREATE_INDEX_RE.match(sql)
    return (match.group("name"), match.group("table")) if match else None


def _idempotent(sql: str) -> str:
    """Rewrite CREATE X to CREATE X IF NOT EXISTS."""
    if re.search(r"IF\s+NOT\s+EXISTS", sql, re.IGNORECASE):
        return sql
    return re.sub(
        r"^(\s*CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+)",
        r"\1IF NOT EXISTS ",
        sql,
        count=1,
        flags=re.IGNORECASE,
    )


def _existing_tables(conn: sqlite3.Connection) -> Set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _harvest_fundamentals_columns(path: Path) -> List[Tuple[str, str]]:
    """Read the `new_fields` list out of the fundamentals migration script."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "new_fields" not in names:
            continue
        try:
            fields = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            return []
        harvested: List[Tuple[str, str]] = []
        for entry in fields:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            name, col_type = str(entry[0]).strip(), str(entry[1]).strip().upper()
            if _IDENTIFIER_RE.match(name) and col_type in _COLUMN_TYPES:
                harvested.append((name, col_type))
        return harvested
    return []


def build_schema(db_path: Path) -> Dict[str, object]:
    """Create every table and index. Returns a small report dict."""
    canonical_path = PROJECT_ROOT / CANONICAL_SCHEMA_SCRIPT
    if not canonical_path.exists():
        raise BootstrapError(
            f"Canonical schema script not found: {canonical_path}\n"
            "This script reuses that file's DDL rather than duplicating it."
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Execute the canonical script against our output path. Its logger and
    #    its overwrite prompt are replaced so it stays silent and unattended.
    module = _load_module(canonical_path, "_dalal_canonical_schema")
    module.DB_FILE = db_path                                    # type: ignore[attr-defined]
    module.log_message = lambda message: _vprint(f"    {message}")  # type: ignore[attr-defined]
    module.input = lambda *_args, **_kwargs: "yes"              # type: ignore[attr-defined]
    print(f"[1/5] Canonical schema  <- {CANONICAL_SCHEMA_SCRIPT.as_posix()}")
    if module.create_schema() is False:                          # type: ignore[attr-defined]
        raise BootstrapError("Canonical schema script reported failure.")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        canonical_tables = _existing_tables(conn)

        # 2. Fill in the tables the canonical script predates, and replace the
        #    ones a later script redefined.
        index_statements: List[str] = [
            sql for sql in _harvest_ddl(canonical_path) if _index_target(sql)
        ]
        added: List[str] = []
        replaced: List[str] = []

        print("[2/5] Supplementary tables")
        for relative in SUPPLEMENTARY_DDL_SCRIPTS:
            source = PROJECT_ROOT / relative
            if not source.exists():
                print(f"      [WARN] missing DDL source: {relative.as_posix()}")
                continue
            for sql in _harvest_ddl(source):
                if _index_target(sql):
                    index_statements.append(sql)
                    continue
                table = _table_of(sql)
                if table is None or table not in EXPECTED_TABLES:
                    continue
                present = table in _existing_tables(conn)
                # A supplementary definition only replaces the canonical one,
                # and only once - a third script declaring the same table is
                # ignored rather than dropping what we just built.
                supersedes = table in OVERRIDE_TABLES and table in canonical_tables
                if present and not supersedes:
                    continue
                if present:
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
                    canonical_tables.discard(table)
                    replaced.append(table)
                else:
                    added.append(table)
                conn.execute(_idempotent(sql))
                _vprint(f"      {table} <- {relative.as_posix()}")
        conn.commit()
        if added:
            print(f"      added:    {', '.join(sorted(set(added)))}")
        if replaced:
            print(f"      replaced: {', '.join(sorted(set(replaced)))} (newer definition wins)")

        # 3. Indexes. Every harvested index whose table survived.
        print("[3/5] Indexes")
        tables_now = _existing_tables(conn)
        index_count = 0
        for sql in index_statements:
            target = _index_target(sql)
            if target is None or target[1] not in tables_now:
                continue
            try:
                conn.execute(_idempotent(sql))
                index_count += 1
            except sqlite3.Error as exc:
                print(f"      [WARN] index {target[0]}: {exc}")
        conn.commit()
        print(f"      {index_count} index statements applied")

        # 4. Post-canonical columns on `fundamentals`.
        print("[4/5] Column migrations")
        migration_path = PROJECT_ROOT / FUNDAMENTALS_MIGRATION_SCRIPT
        migrated = 0
        if migration_path.exists() and "fundamentals" in tables_now:
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(fundamentals)")}
            for name, col_type in _harvest_fundamentals_columns(migration_path):
                if name in existing_cols:
                    continue
                conn.execute(f"ALTER TABLE fundamentals ADD COLUMN {name} {col_type}")
                migrated += 1
            conn.commit()
        print(f"      {migrated} columns added to fundamentals")

        # 5. Reconcile against the contract, loudly.
        print("[5/5] Schema check")
        actual = _existing_tables(conn)
        missing = sorted(EXPECTED_TABLES - actual)
        extra = sorted(actual - EXPECTED_TABLES)

        if extra:
            print()
            print("  !! SCHEMA DRIFT --------------------------------------------------")
            print("  !! These tables are declared by the ingestion scripts but are NOT")
            print("  !! part of the documented 16-table warehouse. They are being")
            print("  !! dropped so this database matches README.md. If one of them is")
            print("  !! now real, add it to EXPECTED_TABLES in this script.")
            for table in extra:
                origin = " (from the canonical schema script)" if table in canonical_tables else ""
                print(f"  !!   - {table}{origin}")
            print("  !! ----------------------------------------------------------------")
            print()
            for table in extra:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.commit()
            actual = _existing_tables(conn)

        if missing:
            print()
            print("  !! SCHEMA INCOMPLETE ---------------------------------------------")
            print("  !! The ingestion scripts no longer define every expected table.")
            for table in missing:
                print(f"  !!   - {table}")
            print("  !! ----------------------------------------------------------------")
            raise BootstrapError(
                f"{len(missing)} expected table(s) could not be created: {', '.join(missing)}"
            )

        print(f"      {len(actual)} tables, all {len(EXPECTED_TABLES)} expected tables present")
        return {
            "tables": sorted(actual),
            "added": sorted(set(added)),
            "replaced": sorted(set(replaced)),
            "dropped": extra,
            "indexes": index_count,
            "fundamentals_columns_added": migrated,
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Copying a bounded slice out of an existing warehouse
# --------------------------------------------------------------------------


#: table -> source columns that the target schema has no home for. Populated
#: while sampling and reported at the end, because it means the ingestion
#: scripts and the built warehouse have drifted apart.
_COLUMN_DRIFT: Dict[str, List[str]] = {}


def _columns(conn: sqlite3.Connection, table: str) -> List[Tuple[str, int]]:
    return [(row[1], row[5]) for row in conn.execute(f"PRAGMA table_info({table})")]


def _copyable_columns(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> List[str]:
    """Columns present in both databases, minus the autoincrement rowid."""
    src_cols = {name for name, _pk in _columns(src, table)}
    dst_cols = _columns(dst, table)
    shared = [name for name, pk in dst_cols if name in src_cols and not (pk and name == "id")]
    dropped = sorted(src_cols - {name for name, _pk in dst_cols})
    if dropped:
        _COLUMN_DRIFT[table] = dropped
    return shared


def _copy_rows(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
    *,
    where: str = "",
    params: Sequence[object] = (),
    order_by: str = "",
    limit: Optional[int] = None,
) -> int:
    """Copy rows from src to dst. Every call is bounded by `limit`."""
    columns = _copyable_columns(src, dst, table)
    if not columns:
        return 0
    column_sql = ", ".join(columns)
    query = f"SELECT {column_sql} FROM {table}"
    if where:
        query += f" WHERE {where}"
    if order_by:
        query += f" ORDER BY {order_by}"
    if limit is not None:
        query += f" LIMIT {int(limit)}"

    placeholders = ", ".join("?" for _ in columns)
    insert = f"INSERT OR IGNORE INTO {table} ({column_sql}) VALUES ({placeholders})"

    cursor = src.execute(query, tuple(params))
    before = dst.total_changes
    while True:
        batch = cursor.fetchmany(2000)
        if not batch:
            break
        dst.executemany(insert, batch)
    dst.commit()
    return dst.total_changes - before


def _copy_by_symbols(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
    symbols: Sequence[str],
    *,
    column: str = "symbol",
    limit_per_chunk: int = 20000,
) -> int:
    total = 0
    for start in range(0, len(symbols), _IN_CHUNK):
        chunk = symbols[start : start + _IN_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        total += _copy_rows(
            src,
            dst,
            table,
            where=f"{column} IN ({placeholders})",
            params=chunk,
            limit=limit_per_chunk,
        )
    return total


def _select_scalars(conn: sqlite3.Connection, query: str, params: Sequence[object] = ()) -> List[str]:
    return [str(row[0]) for row in conn.execute(query, tuple(params)) if row[0] is not None]


def _shift_years(date_text: str, years: int) -> str:
    """ISO date minus N years, without pulling in dateutil."""
    try:
        anchor = datetime.strptime(date_text[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        anchor = datetime.now()
    return (anchor - timedelta(days=365 * years + years // 4)).strftime("%Y-%m-%d")


def sample_from_existing(
    source_db: Path,
    dst: sqlite3.Connection,
    *,
    ohlc_years: int,
    ohlc_symbols: int,
    max_stocks: int,
) -> None:
    """Extract a bounded, resolver-complete slice from a full warehouse.

    Every query here is LIMITed or keyed by an explicit symbol list; the
    source database is several gigabytes and is opened read-only.
    """
    uri = f"file:{source_db.as_posix()}?mode=ro"
    src = sqlite3.connect(uri, uri=True)
    try:
        src_tables = _existing_tables(src)
        required = {"stocks_master", "daily_ohlc"}
        if not required <= src_tables:
            raise BootstrapError(
                f"{source_db} does not look like a Dalal Street warehouse: "
                f"missing {', '.join(sorted(required - src_tables))}."
            )
        missing = sorted(EXPECTED_TABLES - src_tables)
        if missing:
            print(f"  [WARN] source is missing {len(missing)} table(s): {', '.join(missing)}")

        # ---- which symbols ------------------------------------------------
        present_well_known: List[str] = []
        for start in range(0, len(WELL_KNOWN_SYMBOLS), _IN_CHUNK):
            chunk = WELL_KNOWN_SYMBOLS[start : start + _IN_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            present_well_known.extend(
                _select_scalars(
                    src,
                    f"SELECT symbol FROM stocks_master WHERE symbol IN ({placeholders})",
                    chunk,
                )
            )

        max_ohlc_date = src.execute("SELECT MAX(date) FROM daily_ohlc").fetchone()[0]
        cutoff = _shift_years(max_ohlc_date or datetime.now().strftime("%Y-%m-%d"), ohlc_years)

        price_symbols = present_well_known[:ohlc_symbols]
        if len(price_symbols) < ohlc_symbols and max_ohlc_date:
            # Top up from whatever traded on the most recent day in the file.
            for symbol in _select_scalars(
                src,
                "SELECT DISTINCT symbol FROM daily_ohlc WHERE date = ? LIMIT ?",
                (max_ohlc_date, ohlc_symbols * 4),
            ):
                if symbol not in price_symbols:
                    price_symbols.append(symbol)
                if len(price_symbols) >= ohlc_symbols:
                    break

        # Symbols that are the far end of a rename, so the resolver's symbol
        # chase and name-change lookup land on a row that actually exists.
        renamed = _select_scalars(
            src,
            "SELECT DISTINCT s.symbol FROM stocks_master s "
            "JOIN symbol_change_events e ON e.new_symbol = s.symbol LIMIT 150",
        )
        renamed += _select_scalars(
            src,
            "SELECT DISTINCT s.symbol FROM stocks_master s "
            "JOIN name_change_events e ON e.symbol = s.symbol LIMIT 150",
        )

        selected: List[str] = []
        seen: Set[str] = set()
        for symbol in list(price_symbols) + renamed:
            if symbol not in seen:
                seen.add(symbol)
                selected.append(symbol)
        if len(selected) < max_stocks:
            for symbol in _select_scalars(
                src,
                "SELECT symbol FROM stocks_master WHERE is_active = 1 ORDER BY symbol LIMIT ?",
                (max_stocks * 2,),
            ):
                if symbol not in seen:
                    seen.add(symbol)
                    selected.append(symbol)
                if len(selected) >= max_stocks:
                    break
        selected = selected[:max_stocks]

        print(f"  source      : {source_db}  ({_human_bytes(_db_file_size(source_db))})")
        print(f"  stocks      : {len(selected)} symbols")
        print(f"  price slice : {len(price_symbols)} symbols, {cutoff} -> {max_ohlc_date}")

        # ---- reference data ------------------------------------------------
        _copy_by_symbols(src, dst, "stocks_master", selected)

        # Renames are small and load-bearing: take them whole.
        _copy_rows(src, dst, "name_change_events", limit=5000)
        _copy_rows(src, dst, "symbol_change_events", limit=5000)

        # Demergers first (tier 5 needs at least one), then everything else
        # that touches a sampled symbol.
        _copy_rows(
            src,
            dst,
            "corporate_events",
            where="event_type = 'DEMERGER'",
            order_by="ex_date DESC",
            limit=500,
        )
        _copy_by_symbols(src, dst, "corporate_events", selected, limit_per_chunk=5000)
        _copy_rows(src, dst, "delisting_events", limit=2000)

        # ---- prices ---------------------------------------------------------
        ohlc_rows = 0
        for symbol in price_symbols:
            ohlc_rows += _copy_rows(
                src,
                dst,
                "daily_ohlc",
                where="symbol = ? AND date >= ?",
                params=(symbol, cutoff),
                order_by="date",
                limit=ohlc_years * 300,
            )
        print(f"  daily_ohlc  : {ohlc_rows:,} rows")

        for index_name in SAMPLE_INDEX_NAMES:
            _copy_rows(
                src,
                dst,
                "market_indices",
                where="index_name = ? AND date >= ?",
                params=(index_name, cutoff),
                order_by="date",
                limit=ohlc_years * 300,
            )
        for etf_name in SAMPLE_ETF_NAMES:
            _copy_rows(
                src,
                dst,
                "market_etfs",
                where="index_name = ? AND date >= ?",
                params=(etf_name, cutoff),
                order_by="date",
                limit=ohlc_years * 300,
            )

        # ---- fundamentals and the long tail ---------------------------------
        _copy_by_symbols(src, dst, "fundamentals", selected)
        _copy_by_symbols(src, dst, "quarterly_results", selected)
        _copy_by_symbols(src, dst, "annual_financials", selected)
        _copy_rows(src, dst, "ipo_data", order_by="listing_date DESC", limit=500)
        _copy_rows(src, dst, "fii_dii_data", order_by="date DESC", limit=500)
        # bhavcopy_history rows carry large JSON ticker diffs - keep very few.
        _copy_rows(src, dst, "bhavcopy_history", order_by="date DESC", limit=20)
        _copy_rows(src, dst, "download_log", order_by="id DESC", limit=200)
        _copy_rows(src, dst, "metadata", limit=200)

        _stamp_metadata(dst, source=str(source_db), scope="sample")

        if _COLUMN_DRIFT:
            print()
            print("  !! COLUMN DRIFT: the source has columns no ingestion script declares,")
            print("  !! so they were not copied. Add them to the DDL if they matter.")
            for table in sorted(_COLUMN_DRIFT):
                print(f"  !!   {table}: {', '.join(_COLUMN_DRIFT[table])}")
    finally:
        src.close()


# --------------------------------------------------------------------------
# Fallback: pull a minimal slice straight from NSE
# --------------------------------------------------------------------------


def sample_from_network(db_path: Path, dst: sqlite3.Connection, *, days: int = 10) -> None:
    """Last resort when there is no existing database to sample from."""
    guidance = (
        "\nNo local warehouse to sample from, and the live NSE path is not usable.\n"
        "Do one of:\n"
        "  1. python scripts/bootstrap_db.py --schema-only\n"
        "       Empty database with the full schema; the app boots, queries\n"
        "       return nothing until you ingest data.\n"
        "  2. python scripts/bootstrap_db.py --from-existing <path-to-db>\n"
        "       If you already built a warehouse elsewhere.\n"
        "  3. Build the full warehouse from primary sources - docs/REBUILD.md.\n"
    )

    try:
        from App.src.data_fetcher.bhavcopy_downloader import BhavcopyDownloader
    except Exception as exc:
        raise SampleDataUnavailable(
            f"Could not import App/src/data_fetcher/bhavcopy_downloader.py ({exc})."
            f"{guidance}"
        ) from exc

    try:
        from nselib import capital_market
    except Exception as exc:
        raise SampleDataUnavailable(
            f"nselib is not importable ({exc}). Install it with "
            f"`pip install -r App/api/requirements.txt`.{guidance}"
        ) from exc

    print("  fetching equity list from nselib ...")
    try:
        equity_list = capital_market.equity_list()
    except Exception as exc:
        raise SampleDataUnavailable(
            f"nselib could not reach NSE ({exc}). NSE blocks unauthenticated "
            f"traffic aggressively and may simply be refusing this machine.{guidance}"
        ) from exc

    inserted = 0
    for _idx, row in equity_list.iterrows():
        symbol = str(row.get("SYMBOL", "")).strip().upper()
        name = str(row.get("NAME OF COMPANY", "")).strip()
        if not symbol or not name or symbol == name:
            continue
        try:
            dst.execute(
                "INSERT OR IGNORE INTO stocks_master "
                "(symbol, company_name, listing_date, face_value, series, is_active) "
                "VALUES (?, ?, ?, ?, 'EQ', 1)",
                (symbol, name, row.get(" DATE OF LISTING"), row.get(" FACE VALUE")),
            )
            inserted += 1
        except sqlite3.Error:
            continue
    dst.commit()
    print(f"  stocks_master: {inserted:,} rows")
    if inserted == 0:
        raise SampleDataUnavailable(f"nselib returned no usable equities.{guidance}")

    print(f"  fetching up to {days} recent bhavcopies ...")
    downloader = BhavcopyDownloader(
        str(db_path), cache_dir=str(DEFAULT_CACHE_DIR / "bhavcopy")
    )
    day = datetime.now()
    fetched = 0
    for _attempt in range(days * 2):
        if fetched >= days:
            break
        day -= timedelta(days=1)
        if day.weekday() >= 5:  # Saturday, Sunday
            continue
        try:
            downloader.update_daily(day)
            fetched += 1
        except Exception as exc:  # a missing holiday file is not fatal
            _vprint(f"    {day:%Y-%m-%d}: {exc}")

    rows = dst.execute("SELECT COUNT(*) FROM daily_ohlc").fetchone()[0]
    if rows == 0:
        raise SampleDataUnavailable(
            f"Downloaded no OHLC rows from NSE after {days} attempts.{guidance}"
        )
    print(f"  daily_ohlc   : {rows:,} rows")
    print(
        "  [NOTE] A live slice has no rename history, corporate actions, or\n"
        "         index data, so the resolver cannot exercise every tier.\n"
        "         See docs/REBUILD.md for the full build."
    )
    _stamp_metadata(dst, source="nse-live", scope="sample")


def _stamp_metadata(conn: sqlite3.Connection, *, source: str, scope: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for key, value in (
        ("data_scope", scope),
        ("sample_source", source),
        ("sample_built_at", stamp),
    ):
        conn.execute(
            "INSERT INTO metadata (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, value, stamp),
        )
    conn.commit()


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def row_counts(db_path: Path) -> List[Tuple[str, int]]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        tables = sorted(_existing_tables(conn))
        return [
            (table, conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        ]
    finally:
        conn.close()


def print_summary(db_path: Path, title: str = "RESULT") -> None:
    _section(title)
    counts = row_counts(db_path)
    width = max([len(name) for name, _ in counts] + [5])
    print(f"  {'table'.ljust(width)}  {'rows':>12}")
    print(f"  {'-' * width}  {'-' * 12}")
    for name, count in counts:
        marker = "  (empty)" if count == 0 else ""
        print(f"  {name.ljust(width)}  {count:>12,}{marker}")
    print(f"  {'-' * width}  {'-' * 12}")
    print(f"  {'TOTAL'.ljust(width)}  {sum(c for _n, c in counts):>12,}")
    print()
    print(f"  Database : {db_path}")
    print(f"  Size     : {_human_bytes(_db_file_size(db_path))}")
    print(f"  Tables   : {len(counts)}")


def describe_existing(db_path: Path) -> None:
    """What is already there, before we refuse to clobber it."""
    print(f"  Path  : {db_path}")
    print(f"  Size  : {_human_bytes(_db_file_size(db_path))}")
    try:
        for name, count in row_counts(db_path):
            print(f"          {name:<24} {count:>12,}")
    except sqlite3.Error as exc:
        print(f"          (could not read row counts: {exc})")


def _remove_database(db_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = db_path.with_name(db_path.name + suffix)
        if target.exists():
            target.unlink()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bootstrap_db.py",
        description="Create the Dalal Street AI SQLite warehouse.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python scripts/bootstrap_db.py --schema-only\n"
            "  python scripts/bootstrap_db.py --sample\n"
            "  python scripts/bootstrap_db.py --from-existing /data/full.db "
            "--output App/database/sample.db\n"
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--schema-only",
        action="store_true",
        help="Create every table and index, insert no data.",
    )
    mode.add_argument(
        "--sample",
        action="store_true",
        help="Schema plus a small usable slice of data.",
    )
    mode.add_argument(
        "--from-existing",
        metavar="PATH",
        type=Path,
        help="Extract the sample from this existing database (implies --sample).",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Where to write the database (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output database if it already exists.",
    )
    parser.add_argument(
        "--ohlc-years",
        type=int,
        default=2,
        help="Years of daily_ohlc history per sampled symbol (default: 2).",
    )
    parser.add_argument(
        "--ohlc-symbols",
        type=int,
        default=50,
        help="How many symbols get price history (default: 50).",
    )
    parser.add_argument(
        "--max-stocks",
        type=int,
        default=400,
        help="Cap on stocks_master rows in the sample (default: 400).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-statement detail.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    global _VERBOSE
    args = parse_args(argv)
    _VERBOSE = args.verbose

    output = args.output.expanduser()
    if not output.is_absolute():
        output = (PROJECT_ROOT / output).resolve()
    else:
        output = output.resolve()

    wants_sample = bool(args.sample or args.from_existing)

    # Where does sample data come from?
    source_db: Optional[Path] = None
    if wants_sample:
        candidate = args.from_existing or DEFAULT_DB_PATH
        candidate = candidate.expanduser()
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if args.from_existing and not candidate.exists():
            print(f"[ERROR] --from-existing: no such database: {candidate}")
            return 1
        if candidate.exists() and candidate.stat().st_size > 0:
            source_db = candidate
        if source_db is not None and source_db == output:
            print(
                "[ERROR] The sample source and the output are the same file:\n"
                f"        {output}\n"
                "        Refusing to overwrite the database being read.\n"
                "        Pass --output with a different path."
            )
            return 1

    _section("DALAL STREET AI - DATABASE BOOTSTRAP")
    print(f"  Mode   : {'schema-only' if args.schema_only else 'sample'}")
    print(f"  Output : {output}")

    if output.exists():
        if not args.force:
            print()
            print("[REFUSING] A database already exists at the output path.")
            describe_existing(output)
            print()
            print("  Pass --force to overwrite it, or --output to write elsewhere.")
            return 1
        print()
        print("[FORCE] Overwriting the existing database:")
        describe_existing(output)
        _remove_database(output)

    try:
        _section("SCHEMA")
        build_schema(output)

        if wants_sample:
            _section("SAMPLE DATA")
            conn = sqlite3.connect(str(output))
            try:
                conn.execute("PRAGMA foreign_keys = OFF")
                if source_db is not None:
                    sample_from_existing(
                        source_db,
                        conn,
                        ohlc_years=max(1, args.ohlc_years),
                        ohlc_symbols=max(1, args.ohlc_symbols),
                        max_stocks=max(1, args.max_stocks),
                    )
                else:
                    print(
                        "  No existing warehouse found at "
                        f"{DEFAULT_DB_PATH}; falling back to live NSE data."
                    )
                    sample_from_network(output, conn)
            finally:
                conn.close()
            conn = sqlite3.connect(str(output))
            try:
                conn.execute("VACUUM")
            finally:
                conn.close()
    except SampleDataUnavailable as exc:
        print()
        print(f"[ERROR] {exc}")
        _remove_database(output)
        print(f"[CLEANUP] Removed the partial database at {output}.")
        return 1
    except BootstrapError as exc:
        print()
        print(f"[ERROR] {exc}")
        return 1

    print_summary(output)
    print()
    print("  Next: python scripts/verify_db.py" + ("" if output == DEFAULT_DB_PATH else f" --db {output}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
