"""
PHASE 1: CREATE NEW DATABASE SCHEMA
Creates fresh database with validation constraints

Features:
- CHECK constraints (no negative prices, no NULL names)
- FOREIGN KEY constraints (referential integrity)
- UNIQUE constraints (no duplicates)
- Proper indexes (query performance)

Usage:
    python scripts/rebuild/01_create_schema.py
"""

import sqlite3
from pathlib import Path
from datetime import datetime

# Configuration
DB_FILE = Path(__file__).parent.parent.parent / "database" / "stock_market_new.db"
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / f"01_create_schema_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log_message(message):
    """Log to console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)

    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry + '\n')

def create_schema():
    """Create complete database schema"""

    log_message("="*70)
    log_message("CREATING NEW DATABASE SCHEMA")
    log_message("="*70)
    log_message("")

    # Check if file already exists
    if DB_FILE.exists():
        log_message(f"[WARN] Database already exists: {DB_FILE}")
        response = input("Overwrite? (yes/no): ")
        if response.lower() != 'yes':
            log_message("[ABORT] User cancelled")
            return False
        DB_FILE.unlink()
        log_message("[OK] Old database deleted")

    log_message(f"[INFO] Creating: {DB_FILE}")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON")

    # ========================================================================
    # TABLE 1: stocks_master
    # ========================================================================
    log_message("[1/12] Creating: stocks_master")

    cursor.execute('''
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
    ''')

    cursor.execute('CREATE INDEX idx_stocks_symbol ON stocks_master(symbol)')
    cursor.execute('CREATE INDEX idx_stocks_active ON stocks_master(is_active)')

    # ========================================================================
    # TABLE 2: daily_ohlc
    # ========================================================================
    log_message("[2/12] Creating: daily_ohlc")

    cursor.execute('''
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
    ''')

    cursor.execute('CREATE INDEX idx_ohlc_symbol ON daily_ohlc(symbol)')
    cursor.execute('CREATE INDEX idx_ohlc_date ON daily_ohlc(date)')
    cursor.execute('CREATE INDEX idx_ohlc_symbol_date ON daily_ohlc(symbol, date)')

    # ========================================================================
    # TABLE 3: fundamentals
    # ========================================================================
    log_message("[3/12] Creating: fundamentals")

    cursor.execute('''
        CREATE TABLE fundamentals (
            symbol TEXT PRIMARY KEY,
            company_name TEXT,

            -- Market data
            market_cap REAL,
            current_price REAL,
            week52_high REAL,
            week52_low REAL,

            -- Valuation ratios
            pe_ratio REAL,
            pb_ratio REAL,  -- Calculated: current_price / book_value
            book_value REAL,
            face_value REAL,
            dividend_yield REAL,

            -- Performance metrics
            roe REAL,
            roce REAL,
            eps REAL,

            -- Shareholding
            promoter_holding REAL,
            fii_holding REAL,
            dii_holding REAL,

            data_source TEXT DEFAULT 'screener.in',
            last_updated TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (symbol) REFERENCES stocks_master(symbol),

            -- Validation: Reasonable ranges
            CHECK(market_cap IS NULL OR market_cap > 0),
            CHECK(current_price IS NULL OR current_price > 0),
            CHECK(pe_ratio IS NULL OR pe_ratio > 0),
            -- Note: book_value CAN be negative for distressed/bankrupt companies
            -- No constraint on book_value to allow negative values
            CHECK(promoter_holding IS NULL OR (promoter_holding >= 0 AND promoter_holding <= 100)),
            CHECK(fii_holding IS NULL OR (fii_holding >= 0 AND fii_holding <= 100)),
            CHECK(dii_holding IS NULL OR (dii_holding >= 0 AND dii_holding <= 100))
        )
    ''')

    cursor.execute('CREATE INDEX idx_fundamentals_symbol ON fundamentals(symbol)')

    # ========================================================================
    # TABLE 4: quarterly_results
    # ========================================================================
    log_message("[4/12] Creating: quarterly_results")

    cursor.execute('''
        CREATE TABLE quarterly_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            quarter TEXT NOT NULL,
            quarter_date TEXT,

            -- Revenue
            sales REAL,
            other_income REAL,

            -- Expenses
            expenses REAL,
            operating_profit REAL,
            opm_percent REAL,
            interest REAL,
            depreciation REAL,

            -- Profit
            profit_before_tax REAL,
            tax_percent REAL,
            net_profit REAL,
            eps REAL,

            data_source TEXT DEFAULT 'screener.in',
            last_updated TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(symbol, quarter),
            FOREIGN KEY (symbol) REFERENCES stocks_master(symbol)
        )
    ''')

    cursor.execute('CREATE INDEX idx_quarterly_symbol ON quarterly_results(symbol)')
    cursor.execute('CREATE INDEX idx_quarterly_date ON quarterly_results(quarter_date)')

    # ========================================================================
    # TABLE 5: annual_financials
    # ========================================================================
    log_message("[5/12] Creating: annual_financials")

    cursor.execute('''
        CREATE TABLE annual_financials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            year TEXT NOT NULL,
            year_end_date TEXT,

            -- P&L
            sales REAL,
            expenses REAL,
            operating_profit REAL,
            other_income REAL,
            interest REAL,
            depreciation REAL,
            profit_before_tax REAL,
            tax REAL,
            net_profit REAL,
            eps REAL,

            -- Balance Sheet
            equity_capital REAL,
            reserves REAL,
            borrowings REAL,
            total_liabilities REAL,
            fixed_assets REAL,
            investments REAL,
            total_assets REAL,

            -- Cash Flow
            cash_from_operating REAL,
            cash_from_investing REAL,
            cash_from_financing REAL,
            net_cash_flow REAL,

            data_source TEXT DEFAULT 'screener.in',
            last_updated TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(symbol, year),
            FOREIGN KEY (symbol) REFERENCES stocks_master(symbol)
        )
    ''')

    cursor.execute('CREATE INDEX idx_annual_symbol ON annual_financials(symbol)')
    cursor.execute('CREATE INDEX idx_annual_year ON annual_financials(year)')

    # ========================================================================
    # TABLE 6: corporate_actions
    # ========================================================================
    log_message("[6/12] Creating: corporate_actions")

    cursor.execute('''
        CREATE TABLE corporate_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            action_type TEXT NOT NULL,
            subject TEXT,
            ex_date TEXT,
            record_date TEXT,
            bc_start_date TEXT,
            bc_end_date TEXT,
            face_value REAL,

            data_source TEXT DEFAULT 'nselib',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (symbol) REFERENCES stocks_master(symbol)
        )
    ''')

    cursor.execute('CREATE INDEX idx_actions_symbol ON corporate_actions(symbol)')
    cursor.execute('CREATE INDEX idx_actions_type ON corporate_actions(action_type)')
    cursor.execute('CREATE INDEX idx_actions_date ON corporate_actions(ex_date)')

    # ========================================================================
    # TABLE 7: bulk_deals
    # ========================================================================
    log_message("[7/12] Creating: bulk_deals")

    cursor.execute('''
        CREATE TABLE bulk_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            client_name TEXT,
            deal_type TEXT,  -- 'BUY' or 'SELL'
            quantity INTEGER,
            price REAL,

            data_source TEXT DEFAULT 'nselib',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (symbol) REFERENCES stocks_master(symbol)
        )
    ''')

    cursor.execute('CREATE INDEX idx_bulk_symbol ON bulk_deals(symbol)')
    cursor.execute('CREATE INDEX idx_bulk_date ON bulk_deals(trade_date)')

    # ========================================================================
    # TABLE 8: block_deals
    # ========================================================================
    log_message("[8/12] Creating: block_deals")

    cursor.execute('''
        CREATE TABLE block_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            client_name TEXT,
            deal_type TEXT,  -- 'BUY' or 'SELL'
            quantity INTEGER,
            price REAL,

            data_source TEXT DEFAULT 'nselib',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (symbol) REFERENCES stocks_master(symbol)
        )
    ''')

    cursor.execute('CREATE INDEX idx_block_symbol ON block_deals(symbol)')
    cursor.execute('CREATE INDEX idx_block_date ON block_deals(trade_date)')

    # ========================================================================
    # TABLE 9: market_indices
    # ========================================================================
    log_message("[9/12] Creating: market_indices")

    cursor.execute('''
        CREATE TABLE market_indices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_name TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER,

            data_source TEXT DEFAULT 'nselib',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(index_name, date),

            CHECK(open > 0),
            CHECK(high > 0),
            CHECK(low > 0),
            CHECK(close > 0),
            CHECK(high >= low)
        )
    ''')

    cursor.execute('CREATE INDEX idx_indices_name ON market_indices(index_name)')
    cursor.execute('CREATE INDEX idx_indices_date ON market_indices(date)')

    # ========================================================================
    # TABLE 10: india_vix
    # ========================================================================
    log_message("[10/12] Creating: india_vix")

    cursor.execute('''
        CREATE TABLE india_vix (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            vix_value REAL NOT NULL,

            data_source TEXT DEFAULT 'nselib',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            CHECK(vix_value >= 0)
        )
    ''')

    cursor.execute('CREATE INDEX idx_vix_date ON india_vix(date)')

    # ========================================================================
    # TABLE 11: download_log
    # ========================================================================
    log_message("[11/12] Creating: download_log")

    cursor.execute('''
        CREATE TABLE download_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            symbol TEXT,
            status TEXT NOT NULL,  -- 'SUCCESS', 'FAILED', 'SKIPPED'
            records_added INTEGER DEFAULT 0,
            error_message TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('CREATE INDEX idx_log_table ON download_log(table_name)')
    cursor.execute('CREATE INDEX idx_log_symbol ON download_log(symbol)')
    cursor.execute('CREATE INDEX idx_log_timestamp ON download_log(timestamp)')

    # ========================================================================
    # TABLE 12: metadata
    # ========================================================================
    log_message("[12/12] Creating: metadata")

    cursor.execute('''
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Insert initial metadata
    cursor.execute("INSERT INTO metadata (key, value) VALUES ('schema_version', '2.0')")
    cursor.execute("INSERT INTO metadata (key, value) VALUES ('created_at', ?)",
                   (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
    cursor.execute("INSERT INTO metadata (key, value) VALUES ('last_rebuild', ?)",
                   (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))

    # Commit all changes
    conn.commit()
    conn.close()

    log_message("")
    log_message("="*70)
    log_message("SCHEMA CREATION COMPLETE")
    log_message("="*70)
    log_message(f"Database: {DB_FILE}")
    log_message(f"Size: {DB_FILE.stat().st_size / 1024:.2f} KB")
    log_message(f"Tables: 12")
    log_message("")
    log_message("Features:")
    log_message("  [OK] CHECK constraints (no negative prices, no NULL names)")
    log_message("  [OK] FOREIGN KEY constraints (referential integrity)")
    log_message("  [OK] UNIQUE constraints (no duplicates)")
    log_message("  [OK] Indexes created (query performance)")
    log_message("")
    log_message(f"Log file: {LOG_FILE}")
    log_message("")
    log_message("Next: Run 02_master_rebuild.py to download data")
    log_message("="*70)

    return True

if __name__ == "__main__":
    try:
        success = create_schema()
        if success:
            print("\n[SUCCESS] Schema created successfully!")
        else:
            print("\n[CANCELLED] Schema creation cancelled")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
