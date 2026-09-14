"""
Build the tiny synthetic SQLite fixture database used by the test suite.

Why synthetic
-------------
The production database (``App/database/stock_market_new.db``, ~3.9 GB) is
licence-encumbered NSE data and must never be committed or sampled into the
repository. Every row below is invented. Symbols and company names are shaped
like Indian-market data so the resolver's normalisation rules get a realistic
workout, but no real company is represented.

Why generated instead of committed
----------------------------------
``.gitignore`` excludes ``*.db`` repository-wide, so a committed fixture would
be silently dropped and CI would fail on a fresh clone. Generating it costs
milliseconds, keeps the repo binary-free, and guarantees the fixture can never
drift from the generator that documents it. ``tests/conftest.py`` builds it
once per session into a temporary directory.

Schema
------
The DDL is copied verbatim from the production database's ``sqlite_master``
(not hand-written), so the fixture exercises the same CHECK/UNIQUE/FOREIGN KEY
constraints the real data has to satisfy.

Usage
-----
    python tests/fixtures/build_fixture_db.py --out /tmp/fixture_market.db
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Schema - verbatim from the production database (sqlite_master.sql)
# ---------------------------------------------------------------------------

SCHEMA_SQL: list[str] = [
    """
    CREATE TABLE stocks_master (
        symbol TEXT PRIMARY KEY,
        company_name TEXT NOT NULL,
        listing_date TEXT,
        face_value REAL,
        isin TEXT,
        series TEXT DEFAULT 'EQ',
        is_active BOOLEAN DEFAULT 1,
        is_fno BOOLEAN DEFAULT 0,
        is_nifty50 BOOLEAN DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

        -- Validation: No symbol-as-name bugs!
        CHECK(company_name IS NOT NULL AND company_name != ''),
        CHECK(symbol != company_name)
    )
    """,
    "CREATE INDEX idx_stocks_symbol ON stocks_master(symbol)",
    "CREATE INDEX idx_stocks_active ON stocks_master(is_active)",
    """
    CREATE TABLE daily_ohlc (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume INTEGER,
        prev_close REAL,
        data_source TEXT DEFAULT 'openchart',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,

        UNIQUE(symbol, date),
        FOREIGN KEY (symbol) REFERENCES stocks_master(symbol),

        -- Validation: No negative prices!
        CHECK(open > 0),
        CHECK(high > 0),
        CHECK(low > 0),
        CHECK(close > 0),
        CHECK(high >= low),
        CHECK(high >= open),
        CHECK(high >= close),
        CHECK(low <= open),
        CHECK(low <= close)
    )
    """,
    "CREATE INDEX idx_ohlc_symbol ON daily_ohlc(symbol)",
    "CREATE INDEX idx_ohlc_date ON daily_ohlc(date)",
    "CREATE INDEX idx_ohlc_symbol_date ON daily_ohlc(symbol, date)",
    """
    CREATE TABLE market_indices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        index_name TEXT NOT NULL,
        date TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        points_change REAL,
        change_percent REAL,
        volume REAL,
        turnover REAL,
        pe_ratio REAL,
        pb_ratio REAL,
        div_yield REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(index_name, date)
    )
    """,
    "CREATE INDEX idx_indices_name ON market_indices(index_name)",
    "CREATE INDEX idx_indices_date ON market_indices(date)",
    """
    CREATE TABLE market_etfs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        index_name TEXT NOT NULL,
        date TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        points_change REAL,
        change_percent REAL,
        volume REAL,
        turnover REAL,
        pe_ratio REAL,
        pb_ratio REAL,
        div_yield REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(index_name, date)
    )
    """,
    "CREATE INDEX idx_etf_name ON market_etfs(index_name)",
    "CREATE INDEX idx_etf_date ON market_etfs(date)",
    """
    CREATE TABLE symbol_change_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        old_symbol TEXT NOT NULL,
        new_symbol TEXT NOT NULL,
        company_name TEXT,
        change_date TEXT,
        listing_date_old TEXT,
        listing_date_new TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX idx_sce_old_symbol ON symbol_change_events(old_symbol)",
    "CREATE INDEX idx_sce_new_symbol ON symbol_change_events(new_symbol)",
    """
    CREATE TABLE name_change_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        old_name TEXT NOT NULL,
        new_name TEXT NOT NULL,
        change_date TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX idx_nce_symbol ON name_change_events(symbol)",
    "CREATE INDEX idx_nce_old_name ON name_change_events(old_name)",
    "CREATE INDEX idx_nce_new_name ON name_change_events(new_name)",
    """
    CREATE TABLE delisting_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        company_name TEXT,
        last_traded_date TEXT,
        delisting_reason TEXT DEFAULT 'INFERRED',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX idx_de_symbol ON delisting_events(symbol)",
    """
    CREATE TABLE corporate_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        company_name TEXT,
        purpose TEXT NOT NULL,
        event_type TEXT,
        ex_date TEXT,
        record_date TEXT,
        bc_start_date TEXT,
        bc_end_date TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX idx_ce_symbol ON corporate_events(symbol)",
    "CREATE INDEX idx_ce_event_type ON corporate_events(event_type)",
    "CREATE INDEX idx_ce_ex_date ON corporate_events(ex_date)",
]


# ---------------------------------------------------------------------------
# Data - 100% invented. See tests/README.md for the tier-by-tier map.
# ---------------------------------------------------------------------------

# (symbol, company_name, listing_date, face_value, isin, series,
#  is_active, is_fno, is_nifty50)
#
# listing_date uses the production 'DD-MMM-YYYY' format. The demerger tier
# correlates children by |listing_date - ex_date| <= 30 days, so every
# listing_date outside the two demerger windows is deliberately years away.
STOCKS_MASTER = [
    # Tier 1 - plain active tickers
    ("AGNIMOTORS", "Agni Motors Limited", "12-MAR-2004", 2.0, "INE001Z01011", "EQ", 1, 1, 1),
    ("VAYUCEM", "Vayu Cements Limited", "05-JUL-1996", 10.0, "INE002Z01019", "EQ", 1, 0, 0),
    ("NILAYAM", "Nilayam Housing Finance Limited", "11-DEC-2015", 5.0, "INE003Z01017", "EQ", 1, 0, 0),
    # Tier 2 - terminal symbol of the PURVAENG -> PURVAINFRA -> SETUINFRA chain
    ("SETUINFRA", "Setu Infrastructure Limited", "21-NOV-2011", 1.0, "INE004Z01015", "EQ", 1, 0, 0),
    # Tier 2.5 vs 2.6 ordering regression - collides with 'NIFTY FINANCIAL SERVICES'
    ("JIVANFIN", "Jivan Financial Services Limited", "08-AUG-2017", 10.0, "INE005Z01012", "EQ", 1, 1, 0),
    # Tier 2.5 - fuzzy symbol match target ('TARAPHARM' -> 'TARAPHARMA')
    ("TARAPHARMA", "Tara Pharmaceuticals Limited", "14-FEB-2009", 5.0, "INE006Z01010", "EQ", 1, 0, 0),
    # Tier 3 - name change targets
    ("KAVERITEX", "Kaveri Textiles Limited", "19-JUL-1998", 10.0, "INE007Z01018", "EQ", 1, 0, 0),
    ("RUDRACHEM", "Rudra Speciality Chemicals Limited", "30-JAN-2013", 2.0, "INE008Z01016", "EQ", 1, 0, 0),
    # Tier 4 - present but delisted, proves Tier 1 honours is_active
    ("ORIONMET", "Orion Metals Limited", "03-SEP-2006", 10.0, "INE009Z01014", "EQ", 0, 0, 0),
    # Tier 5 - demerger children (listing dates inside the ex_date windows)
    ("HIMGIRIRE", "Himgiri Realty Limited", "20-JAN-2023", 1.0, "INE010Z01012", "EQ", 1, 0, 0),
    ("MERUCHEM", "Meru Chemicals Limited", "18-AUG-2021", 5.0, "INE011Z01010", "EQ", 1, 0, 0),
    ("MERUPOWER", "Meru Power Limited", "25-AUG-2021", 5.0, "INE012Z01018", "EQ", 1, 0, 0),
]

# Tier 2: a two-hop chain is mandatory - a single hop would pass even if the
# recursion in TickerResolver.resolve() were removed.
SYMBOL_CHANGE_EVENTS = [
    ("PURVAENG", "PURVAINFRA", "Purva Engineering Limited", "14-JUN-2019", None, None),
    ("PURVAINFRA", "SETUINFRA", "Setu Infrastructure Limited", "08-MAR-2022", None, None),
    # single-hop control case
    ("VAYUCEMENT", "VAYUCEM", "Vayu Cements Limited", "22-SEP-2015", None, None),
]

# Tier 3: 'KAVERI MILLS' hits the exact LIKE branch; 'Rudra Chemcials Ltd'
# (deliberate typo) misses LIKE and falls through to the >=75 fuzzy branch.
NAME_CHANGE_EVENTS = [
    ("KAVERITEX", "Kaveri Mills Limited", "Kaveri Textiles Limited", "19-JUL-2016"),
    ("RUDRACHEM", "Rudra Chemicals Limited", "Rudra Speciality Chemicals Limited", "05-APR-2018"),
    ("NILAYAM", "Nilayam Griha Finance Limited", "Nilayam Housing Finance Limited", "27-OCT-2020"),
]

# Tier 4
DELISTING_EVENTS = [
    ("ORIONMET", "Orion Metals Limited", "12-JUN-2019",
     "Voluntary delisting under SEBI Delisting Regulations"),
    ("ZENITHAGRO", "Zenith Agro Industries Limited", "27-NOV-2020", "INFERRED"),
]

# Tier 5: HIMGIRICON produces exactly one child, MERUGROUP produces two.
# ex_date uses the production 'DD-MMM-YYYY' (mixed case) format.
CORPORATE_EVENTS = [
    ("HIMGIRICON", "Himgiri Constructions Limited", "Scheme Of Arrangement-Demerger",
     "DEMERGER", "15-Jan-2023", "18-Jan-2023", "16-Jan-2023", "19-Jan-2023"),
    ("MERUGROUP", "Meru Group Limited", "Demerger Of Business Undertakings",
     "DEMERGER", "10-Aug-2021", "12-Aug-2021", "11-Aug-2021", "13-Aug-2021"),
    # non-demerger noise, so the event_type filter has something to exclude
    ("AGNIMOTORS", "Agni Motors Limited", "Interim Dividend Rs 4 Per Share",
     "DIVIDEND", "05-Feb-2024", "06-Feb-2024", None, None),
]

# Tier 2.6: 'NIFTY 50' also registers the bare 'NIFTY' alias, and 'NIFTY BANK'
# registers 'BANKNIFTY'. 'NIFTY FINANCIAL SERVICES' is the decoy that must lose
# to the JIVANFIN stock. Only one index may contain '50', otherwise the bare
# 'NIFTY' alias becomes ambiguous.
INDEX_NAMES = [
    "NIFTY 50",
    "NIFTY BANK",
    "NIFTY FINANCIAL SERVICES",
    "NIFTY IT",
    "NIFTY MIDCAP 100",
]

# Tier 1b/1c: TickerResolver._load_etf_symbols() reads market_etfs.index_name
# and keeps only the '-EQ' rows, stripping the suffix. The production table
# carries both variants, so the fixture does too.
ETF_NAMES = [
    "NIFTYBEES-EQ",
    "NIFTYBEES",
    "GOLDBEES-EQ",
    "GOLDBEES",
    "BANKBEES-EQ",
    "BANKBEES",
]

TRADING_DATES = [
    "2024-01-01",
    "2024-01-02",
    "2024-01-03",
    "2024-01-04",
    "2024-01-05",
]

# Symbols that get a price series. Kept small on purpose - the query-builder
# tests only need real identifiers to validate against.
OHLC_SYMBOLS = [
    ("AGNIMOTORS", 940.0),
    ("VAYUCEM", 318.5),
    ("SETUINFRA", 76.25),
    ("TARAPHARMA", 1204.0),
    ("JIVANFIN", 262.0),
]

# Deterministic multipliers - no randomness, so the fixture is reproducible.
_DRIFT = [1.000, 1.012, 0.994, 1.021, 1.008]


def _ohlc_rows():
    """Yield daily_ohlc rows that satisfy every CHECK constraint."""
    for symbol, base in OHLC_SYMBOLS:
        prev_close = None
        for i, date in enumerate(TRADING_DATES):
            close = round(base * _DRIFT[i], 2)
            open_ = round(close * 0.997, 2)
            high = round(max(open_, close) * 1.006, 2)
            low = round(min(open_, close) * 0.994, 2)
            volume = 100_000 + (i * 12_500)
            yield (symbol, date, open_, high, low, close, volume, prev_close, "bhavcopy")
            prev_close = close


def _index_rows(names, base_start=18_000.0, step=1_450.0):
    """Yield market_indices / market_etfs rows (identical column layout)."""
    for j, name in enumerate(names):
        base = base_start + (j * step)
        prev = None
        for i, date in enumerate(TRADING_DATES):
            close = round(base * _DRIFT[i], 2)
            open_ = round(close * 0.998, 2)
            high = round(max(open_, close) * 1.004, 2)
            low = round(min(open_, close) * 0.996, 2)
            points_change = round(close - prev, 2) if prev is not None else 0.0
            change_percent = round((points_change / prev) * 100, 4) if prev else 0.0
            yield (
                name, date, open_, high, low, close,
                points_change, change_percent,
                float(2_500_000 + i * 10_000), float(45_000 + i * 500),
                21.4, 3.7, 1.25,
            )
            prev = close


def build(dest: Path) -> Path:
    """
    Create the fixture database at ``dest``, replacing any existing file.

    Returns the path written.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    conn = sqlite3.connect(str(dest))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for statement in SCHEMA_SQL:
            conn.execute(statement)

        conn.executemany(
            "INSERT INTO stocks_master "
            "(symbol, company_name, listing_date, face_value, isin, series, "
            " is_active, is_fno, is_nifty50) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            STOCKS_MASTER,
        )
        conn.executemany(
            "INSERT INTO symbol_change_events "
            "(old_symbol, new_symbol, company_name, change_date, "
            " listing_date_old, listing_date_new) VALUES (?,?,?,?,?,?)",
            SYMBOL_CHANGE_EVENTS,
        )
        conn.executemany(
            "INSERT INTO name_change_events "
            "(symbol, old_name, new_name, change_date) VALUES (?,?,?,?)",
            NAME_CHANGE_EVENTS,
        )
        conn.executemany(
            "INSERT INTO delisting_events "
            "(symbol, company_name, last_traded_date, delisting_reason) "
            "VALUES (?,?,?,?)",
            DELISTING_EVENTS,
        )
        conn.executemany(
            "INSERT INTO corporate_events "
            "(symbol, company_name, purpose, event_type, ex_date, record_date, "
            " bc_start_date, bc_end_date) VALUES (?,?,?,?,?,?,?,?)",
            CORPORATE_EVENTS,
        )
        conn.executemany(
            "INSERT INTO daily_ohlc "
            "(symbol, date, open, high, low, close, volume, prev_close, data_source) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            list(_ohlc_rows()),
        )
        conn.executemany(
            "INSERT INTO market_indices "
            "(index_name, date, open, high, low, close, points_change, "
            " change_percent, volume, turnover, pe_ratio, pb_ratio, div_yield) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            list(_index_rows(INDEX_NAMES)),
        )
        conn.executemany(
            "INSERT INTO market_etfs "
            "(index_name, date, open, high, low, close, points_change, "
            " change_percent, volume, turnover, pe_ratio, pb_ratio, div_yield) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            list(_index_rows(ETF_NAMES, base_start=245.0, step=31.0)),
        )
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()

    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent / "fixture_market.db"),
        help="Destination path for the generated fixture database",
    )
    args = parser.parse_args()

    path = build(Path(args.out))
    size_kb = path.stat().st_size / 1024
    print(f"[OK] Fixture database written: {path} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
