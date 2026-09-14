#!/usr/bin/env python3
"""
Export a publishable dataset from the Dalal Street AI warehouse.

    python scripts/export_dataset.py --tier core
    python scripts/export_dataset.py --tier market --format both --compress
    python scripts/export_dataset.py --tier full --i-understand-fundamentals-are-scraped

Why tiers exist
---------------
The tables in this warehouse do not carry the same redistribution risk, and
lumping them together would be dishonest. See docs/DATA.md for the analysis.

    core    NSE corporate-filing derived. Public-record regulatory
            disclosures: listings, renames, symbol changes, corporate
            actions, delistings, IPOs. This is the project's actual
            differentiator and the most defensible thing to publish.

    market  core + NSE price history (OHLC, indices, ETFs, FII/DII flows).
            Restricted by NSE's data policy, though widely republished.

    full    market + fundamentals scraped from screener.in, a live third-party
            commercial aggregator. Highest risk by a wide margin, and only
            ~48K rows out of ~18M. Requires an explicit acknowledgement flag.

Operational tables (bhavcopy_history, download_log, metadata) are never
exported - they describe this machine's ingestion runs and mean nothing to
anyone else.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, TextIO, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from App.config import config  # noqa: E402
    DEFAULT_DB = Path(config.DB_PATH)
except Exception:  # pragma: no cover - config is optional for a pure export
    DEFAULT_DB = PROJECT_ROOT / "App" / "database" / "stock_market_new.db"


# --------------------------------------------------------------------------
# Tier definitions
# --------------------------------------------------------------------------

TIER_CORE: Tuple[str, ...] = (
    "stocks_master",
    "name_change_events",
    "symbol_change_events",
    "corporate_events",
    "delisting_events",
    "ipo_data",
)

TIER_MARKET_EXTRA: Tuple[str, ...] = (
    "daily_ohlc",
    "market_indices",
    "market_etfs",
    "fii_dii_data",
)

TIER_FULL_EXTRA: Tuple[str, ...] = (
    "fundamentals",
    "quarterly_results",
    "annual_financials",
)

TIERS: Dict[str, Tuple[str, ...]] = {
    "core": TIER_CORE,
    "market": TIER_CORE + TIER_MARKET_EXTRA,
    "full": TIER_CORE + TIER_MARKET_EXTRA + TIER_FULL_EXTRA,
}

NEVER_EXPORT = frozenset({"bhavcopy_history", "download_log", "metadata", "sqlite_sequence"})

# Per-table provenance, kept in step with docs/DATA.md.
PROVENANCE: Dict[str, str] = {
    "stocks_master": "NSE India - 'Securities available for trading' master list",
    "name_change_events": "NSE India - 'Changes in Company Names' filing",
    "symbol_change_events": "NSE India - 'Changes in Symbols' filing",
    "corporate_events": "NSE India - corporate filings / actions (CF-CA export)",
    "delisting_events": "NSE India - delisting notices",
    "ipo_data": "Chittorgarh IPO performance tracker (third-party aggregator)",
    "daily_ohlc": "NSE India daily bhavcopy + OpenChart historical backfill",
    "market_indices": "NSE India archives (ind_close_all) + OpenChart",
    "market_etfs": "NSE India archives + OpenChart",
    "fii_dii_data": "NSE India archives via nselib",
    "fundamentals": "screener.in (third-party commercial aggregator) - SCRAPED",
    "quarterly_results": "screener.in (third-party commercial aggregator) - SCRAPED",
    "annual_financials": "screener.in (third-party commercial aggregator) - SCRAPED",
}

# Columns whose presence would mean personal data is about to be published.
PII_TOKENS = ("email", "phone", "mobile", "client_name", "address", "pan_no", "pan_number", "contact")

DATE_COLUMN_CANDIDATES = ("date", "change_date", "listing_date", "ex_date", "trade_date")

CHUNK = 50_000


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

def _section(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:,.1f} TB"


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Database inspection
# --------------------------------------------------------------------------

def _connect_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(
            f"Source database not found: {db_path}\n"
            f"Build one first:  python scripts/bootstrap_db.py --sample\n"
            f"See docs/REBUILD.md"
        )
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _tables(conn: sqlite3.Connection) -> List[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r[0] for r in cur.fetchall()]


def _columns(conn: sqlite3.Connection, table: str) -> List[Tuple[str, str]]:
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    return [(r[1], (r[2] or "").upper() or "TEXT") for r in cur.fetchall()]


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _date_range(conn: sqlite3.Connection, table: str, columns: Sequence[str]) -> Optional[Tuple[str, str]]:
    for candidate in DATE_COLUMN_CANDIDATES:
        if candidate in columns:
            try:
                lo, hi = conn.execute(
                    f'SELECT MIN("{candidate}"), MAX("{candidate}") FROM "{table}"'
                ).fetchone()
            except sqlite3.Error:
                return None
            if lo and hi:
                return str(lo), str(hi)
            return None
    return None


def _check_pii(table: str, columns: Sequence[str]) -> List[str]:
    hits = []
    for col in columns:
        low = col.lower()
        for token in PII_TOKENS:
            if token in low:
                hits.append(col)
                break
    return hits


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

def _open_text(path: Path, compress: bool) -> TextIO:
    if compress:
        return gzip.open(path, "wt", newline="", encoding="utf-8")
    return path.open("w", newline="", encoding="utf-8")


def _stream_csv(
    conn: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    out_path: Path,
    *,
    compress: bool,
    total: int,
) -> int:
    """Stream a table to CSV. Never loads the full table into memory."""
    written = 0
    quoted = ", ".join(f'"{c}"' for c in columns)
    cur = conn.execute(f'SELECT {quoted} FROM "{table}"')
    with _open_text(out_path, compress) as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        while True:
            rows = cur.fetchmany(CHUNK)
            if not rows:
                break
            writer.writerows(rows)
            written += len(rows)
            if total > CHUNK:
                pct = (written / total * 100) if total else 100.0
                print(f"    {table}: {written:,} / {total:,} rows ({pct:.0f}%)", end="\r", flush=True)
    if total > CHUNK:
        print(" " * 70, end="\r")
    return written


def _stream_parquet(
    conn: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    out_path: Path,
    *,
    total: int,
) -> Optional[int]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return None

    written = 0
    quoted = ", ".join(f'"{c}"' for c in columns)
    cur = conn.execute(f'SELECT {quoted} FROM "{table}"')
    writer = None
    try:
        while True:
            rows = cur.fetchmany(CHUNK)
            if not rows:
                break
            cols = list(zip(*rows)) if rows else [() for _ in columns]
            batch = pa.table({name: pa.array(list(values)) for name, values in zip(columns, cols)})
            if writer is None:
                writer = pq.ParquetWriter(out_path, batch.schema, compression="snappy")
            writer.write_table(batch)
            written += len(rows)
            if total > CHUNK:
                print(f"    {table} (parquet): {written:,} / {total:,}", end="\r", flush=True)
    finally:
        if writer is not None:
            writer.close()
    if total > CHUNK:
        print(" " * 70, end="\r")
    return written


# --------------------------------------------------------------------------
# Dataset card
# --------------------------------------------------------------------------

def _render_card(tier: str, manifest: dict, repo_url: str) -> str:
    tables = manifest["tables"]
    total_rows = sum(t["rows"] for t in tables.values())
    total_bytes = sum(t["bytes"] for t in tables.values())

    lines: List[str] = []
    a = lines.append

    a("# Indian Stock Market — NSE corporate actions, ticker lineage and price history")
    a("")
    a("A structured dataset of India's National Stock Exchange (NSE), assembled from primary")
    a("filings and market archives. Its distinguishing feature is **ticker lineage**: the")
    a("mapping between company names and trading symbols as they change over time, which is")
    a("what makes historical Indian market data usable at all.")
    a("")
    a(f"- **Tier:** `{tier}`")
    a(f"- **Tables:** {len(tables)}")
    a(f"- **Rows:** {total_rows:,}")
    a(f"- **Size:** {_human_bytes(total_bytes)}")
    a(f"- **Exported:** {manifest['exported_utc']}")
    a(f"- **Code:** {repo_url}")
    a("")
    a("## Why this exists")
    a("")
    a("Most Indian-market datasets break the moment a company renames itself or changes its")
    a("symbol. Historical prices are filed under the old symbol; anyone searching uses the new")
    a("name; and the mapping is published by NSE as a pile of disconnected CSVs. This dataset")
    a("joins them, including multi-hop chains where a symbol changed more than once, plus")
    a("demergers, delistings and IPO history.")
    a("")
    a("Companion benchmark: a labelled ticker-resolution evaluation set is published alongside")
    a("this dataset, with categories for renamed companies, changed symbols, multi-hop chains,")
    a("demerger children and delistings.")
    a("")
    a("## Tables")
    a("")

    for name in sorted(tables):
        info = tables[name]
        a(f"### `{name}`")
        a("")
        a(f"- **Rows:** {info['rows']:,}")
        if info.get("date_range"):
            lo, hi = info["date_range"]
            a(f"- **Date range:** {lo} → {hi}")
        a(f"- **File:** `{info['file']}` ({_human_bytes(info['bytes'])})")
        a(f"- **Source:** {info['provenance']}")
        a("")
        a("| Column | Type |")
        a("|---|---|")
        for col, typ in info["columns"]:
            a(f"| `{col}` | {typ} |")
        a("")

    a("## Licensing — read this before you reuse it")
    a("")
    a("**The code that produced this dataset is Apache-2.0. This data is not.**")
    a("")
    a("The underlying market data originates with NSE India and, for some tables, third-party")
    a("aggregators. NSE's data policy permits use for personal, non-commercial and educational")
    a("purposes with attribution, and requires a separate commercial agreement for anything")
    a("beyond that. Redistribution rights were not granted to this dataset's publisher and are")
    a("not granted onward to you.")
    a("")
    a("Practically:")
    a("")
    a("- Research, learning, benchmarking, and academic work — the intended use")
    a("- Commercial products, resale, or paid API access — get an NSE Data agreement first")
    a("- Treat every figure as best-effort. It is not audited, not real-time, and may be wrong")
    a("")
    if tier == "full":
        a("> **This is a `full` tier export.** It contains `fundamentals`, `quarterly_results`")
        a("> and `annual_financials`, which were scraped from screener.in — a third-party")
        a("> commercial aggregator whose own product is this data. Publishing these carries")
        a("> materially more risk than the rest of the dataset.")
        a("")
    a("Per-table provenance is listed above and analysed in `docs/DATA.md` in the repository.")
    a("")
    a("## Rebuilding this yourself")
    a("")
    a("Every ingestion script is open source. You can rebuild the entire warehouse from NSE")
    a("primary sources rather than trusting this export — see `docs/REBUILD.md`.")
    a("")
    a("```bash")
    a(f"git clone {repo_url}")
    a("cd dalal-street-ai")
    a("python scripts/bootstrap_db.py --schema-only")
    a("# then follow docs/REBUILD.md")
    a("```")
    a("")
    a("Load this export into that schema, or query the CSVs directly.")
    a("")
    a("## Verifying integrity")
    a("")
    a("```bash")
    a("sha256sum -c CHECKSUMS.txt")
    a("```")
    a("")
    a("`manifest.json` records the source database fingerprint, row counts and per-file hashes.")
    a("")
    a("## Citation")
    a("")
    a("```bibtex")
    a("@misc{dalal_street_ai_dataset,")
    a("  title  = {Dalal Street AI: NSE corporate actions, ticker lineage and price history},")
    a(f"  year   = {{{datetime.now(timezone.utc).year}}},")
    a(f"  url    = {{{repo_url}}},")
    a(f"  note   = {{Tier: {tier}. Data sourced from NSE India and public filings.}}")
    a("}")
    a("```")
    a("")
    a("## Disclaimer")
    a("")
    a("Not investment advice. Not affiliated with or endorsed by NSE India. Provided as-is with")
    a("no warranty of accuracy or completeness.")
    a("")
    return "\n".join(lines)


DATA_LICENSE_NOTICE = """\
DATA LICENCE NOTICE
===================

This dataset is NOT covered by the Apache-2.0 licence that applies to the
source code of the project that produced it.

The underlying data originates with:

  - NSE India (National Stock Exchange of India Ltd.) - market data,
    corporate filings, and archives.
  - Third-party aggregators, where noted per table in README.md.

NSE asserts copyright over its content. Its published policy permits viewing,
downloading and use for personal, non-commercial or educational purposes,
unmodified and with acknowledgement of source, and requires a separate
commercial agreement for redistribution or commercial use.

  https://www.nseindia.com/static/nse-copyright
  https://www.nseindia.com/static/market-data/nse-data-policy

The publisher of this dataset holds no redistribution licence and grants none
onward. It is made available for research and educational use. If you intend
any commercial use, obtain your own agreement with NSE Data first.

If you are a rights holder and believe this dataset oversteps, open an issue
on the source repository and it will be addressed promptly.

This is not legal advice.
"""


# --------------------------------------------------------------------------
# Main export
# --------------------------------------------------------------------------

def export(
    db_path: Path,
    out_dir: Path,
    tier: str,
    *,
    fmt: str,
    compress: bool,
    repo_url: str,
) -> int:
    conn = _connect_ro(db_path)
    try:
        available = set(_tables(conn))
        wanted = [t for t in TIERS[tier] if t not in NEVER_EXPORT]
        missing = [t for t in wanted if t not in available]
        present = [t for t in wanted if t in available]

        if missing:
            print(f"  ! Not in source database, skipping: {', '.join(missing)}")
        if not present:
            raise SystemExit("Nothing to export - none of the tier's tables exist in the source.")

        # PII gate, before anything is written.
        for table in present:
            cols = [c for c, _ in _columns(conn, table)]
            hits = _check_pii(table, cols)
            if hits:
                raise SystemExit(
                    f"REFUSING TO EXPORT.\n"
                    f"Table '{table}' has columns that look like personal data: {', '.join(hits)}\n"
                    f"Review them and either drop the columns or remove the table from the tier."
                )

        out_dir.mkdir(parents=True, exist_ok=True)
        data_dir = out_dir / "data"
        data_dir.mkdir(exist_ok=True)

        want_csv = fmt in ("csv", "both")
        want_parquet = fmt in ("parquet", "both")
        if want_parquet:
            try:
                import pyarrow  # noqa: F401
            except ImportError:
                print("  ! pyarrow not installed - falling back to CSV only")
                want_parquet = False
                want_csv = True

        stat = db_path.stat()
        manifest: dict = {
            "dataset": "dalal-street-ai",
            "tier": tier,
            "exported_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": {
                # Hashing 3.9 GB takes minutes; size+mtime is a sufficient fingerprint
                # for provenance and is what we record instead.
                "fingerprint_method": "size+mtime",
                "bytes": stat.st_size,
                "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            },
            "tables": {},
        }

        _section(f"EXPORTING TIER '{tier}' — {len(present)} tables")

        for table in present:
            cols_typed = _columns(conn, table)
            cols = [c for c, _ in cols_typed]
            total = _row_count(conn, table)
            drange = _date_range(conn, table, cols)

            suffix = ".csv.gz" if compress else ".csv"
            csv_name = f"{table}{suffix}"
            csv_path = data_dir / csv_name
            primary_name, primary_path = csv_name, csv_path

            print(f"  {table}: {total:,} rows, {len(cols)} columns")

            written = 0
            if want_csv:
                written = _stream_csv(conn, table, cols, csv_path, compress=compress, total=total)
            if want_parquet:
                pq_path = data_dir / f"{table}.parquet"
                pw = _stream_parquet(conn, table, cols, pq_path, total=total)
                if pw is not None and not want_csv:
                    primary_name, primary_path = pq_path.name, pq_path
                    written = pw

            manifest["tables"][table] = {
                "rows": written,
                "columns": cols_typed,
                "date_range": list(drange) if drange else None,
                "file": primary_name,
                "bytes": primary_path.stat().st_size if primary_path.exists() else 0,
                "sha256": _sha256_file(primary_path) if primary_path.exists() else None,
                "provenance": PROVENANCE.get(table, "unknown"),
            }

        # Schema-only SQLite so users can load the CSVs into a real database.
        print("\n  Building schema-only SQLite companion...")
        schema_db = out_dir / "schema_only.db"
        try:
            from scripts.bootstrap_db import build_schema  # type: ignore
            if schema_db.exists():
                schema_db.unlink()
            build_schema(schema_db)
            print(f"    schema_only.db ({_human_bytes(schema_db.stat().st_size)})")
        except Exception as exc:  # pragma: no cover
            print(f"    ! could not build schema companion: {exc}")

        # Card, licence, checksums.
        (out_dir / "README.md").write_text(_render_card(tier, manifest, repo_url), encoding="utf-8")
        (out_dir / "LICENSE.txt").write_text(DATA_LICENSE_NOTICE, encoding="utf-8")
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        checksum_lines = []
        for path in sorted(out_dir.rglob("*")):
            if path.is_file() and path.name != "CHECKSUMS.txt":
                rel = path.relative_to(out_dir).as_posix()
                checksum_lines.append(f"{_sha256_file(path)}  {rel}")
        (out_dir / "CHECKSUMS.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

        total_rows = sum(t["rows"] for t in manifest["tables"].values())
        total_bytes = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())

        _section("EXPORT COMPLETE")
        print(f"  Tier          : {tier}")
        print(f"  Tables        : {len(manifest['tables'])}")
        print(f"  Rows          : {total_rows:,}")
        print(f"  Size on disk  : {_human_bytes(total_bytes)}")
        print(f"  Output        : {out_dir}")
        print()
        print("  Before uploading anywhere, read docs/DATA.md.")
        print("  The data is not covered by this project's Apache-2.0 code licence.")
        return 0
    finally:
        conn.close()


FUNDAMENTALS_WARNING = """
{bar}
STOP — `--tier full` needs an explicit acknowledgement
{bar}

Tier `full` adds three tables to the export:

    fundamentals          ~2,600 rows
    quarterly_results     ~24,700 rows
    annual_financials     ~20,700 rows

These were **scraped from screener.in**, a live third-party commercial
aggregator. That data is not NSE's and it is not yours — it is the product of
someone else's collection and normalisation work, and republishing an
aggregator's own output is the highest-risk category of scraping activity.

Weigh that against what you gain: roughly 48,000 rows out of ~18 million.
It is the *least* valuable part of this dataset by volume and the *most*
likely to draw a takedown.

The `market` tier gives you everything else — price history, indices, ETFs,
corporate actions, ticker lineage — with materially less exposure:

    python scripts/export_dataset.py --tier market

If you have read docs/DATA.md, understand the position, and still want the
fundamentals included, re-run with:

    --i-understand-fundamentals-are-scraped

{bar}
""".format(bar="=" * 68)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Export a publishable dataset from the Dalal Street AI warehouse.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Read docs/DATA.md before publishing anything this produces.",
    )
    p.add_argument("--tier", required=True, choices=sorted(TIERS), help="Which tables to export.")
    p.add_argument("--output", type=Path, default=PROJECT_ROOT / "dist" / "dataset",
                   help="Output directory (default: dist/dataset/).")
    p.add_argument("--format", dest="fmt", choices=("csv", "parquet", "both"), default="csv")
    p.add_argument("--compress", action="store_true", help="gzip the CSV output.")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help="Source database.")
    p.add_argument("--force", action="store_true", help="Overwrite an existing output directory.")
    p.add_argument("--repo-url", default="https://github.com/Sudhanshu614/dalal-street-ai",
                   help="Repository URL written into the dataset card.")
    p.add_argument("--i-understand-fundamentals-are-scraped", dest="ack_scraped",
                   action="store_true", help="Required for --tier full.")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if args.tier == "full" and not args.ack_scraped:
        print(FUNDAMENTALS_WARNING)
        return 1

    out_dir: Path = args.output
    if out_dir.exists() and any(out_dir.iterdir()):
        if not args.force:
            print(f"Output directory is not empty: {out_dir}")
            print("Use --force to overwrite, or choose another --output.")
            return 1
        shutil.rmtree(out_dir)

    return export(
        db_path=Path(args.db),
        out_dir=out_dir,
        tier=args.tier,
        fmt=args.fmt,
        compress=args.compress,
        repo_url=args.repo_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())
