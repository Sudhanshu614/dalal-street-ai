"""
PHASE 5: MIGRATE BOOK VALUE CONSTRAINT
Removes CHECK constraint on book_value to allow negative values

Background:
- 80 stocks failed with "CHECK constraint failed: book_value"
- These are bankrupt/distressed companies with negative book values (ABAN, IDEA, RCOM, UNITECH, etc.)
- Scraper DOES fetch other valid data (market_cap, current_price, PE ratio, promoter_holding)
- But CHECK constraint rejects ENTIRE record if book_value < 0
- Solution: Remove book_value constraint to save partial fundamentals

Usage:
    python scripts/rebuild/05_migrate_book_value_constraint.py
"""

import sqlite3
from pathlib import Path
from datetime import datetime

DB_FILE = Path(__file__).parent.parent.parent / "database" / "stock_market_new.db"

def log_message(message):
    print(message)

def migrate_schema():
    log_message("="*70)
    log_message("MIGRATE BOOK VALUE CONSTRAINT")
    log_message("="*70)
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found")
        return False

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    try:
        # Step 1: Create new fundamentals table without book_value CHECK constraint
        log_message("[1/5] Creating new fundamentals table...")

        cursor.execute('''
            CREATE TABLE fundamentals_new (
                symbol TEXT PRIMARY KEY,
                company_name TEXT,

                -- Market data
                market_cap REAL,
                current_price REAL,
                week52_high REAL,
                week52_low REAL,

                -- Valuation ratios
                pe_ratio REAL,
                pb_ratio REAL,
                book_value REAL,  -- CAN BE NEGATIVE!
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
                -- No constraint on book_value
                CHECK(promoter_holding IS NULL OR (promoter_holding >= 0 AND promoter_holding <= 100)),
                CHECK(fii_holding IS NULL OR (fii_holding >= 0 AND fii_holding <= 100)),
                CHECK(dii_holding IS NULL OR (dii_holding >= 0 AND dii_holding <= 100))
            )
        ''')
        log_message("[OK] Created fundamentals_new")

        # Step 2: Copy all existing data
        log_message("[2/5] Copying existing data...")

        cursor.execute('''
            INSERT INTO fundamentals_new
            SELECT * FROM fundamentals
        ''')

        existing_count = cursor.rowcount
        log_message(f"[OK] Copied {existing_count} records")

        # Step 3: Drop old table
        log_message("[3/5] Dropping old table...")
        cursor.execute('DROP TABLE fundamentals')
        log_message("[OK] Dropped fundamentals")

        # Step 4: Rename new table
        log_message("[4/5] Renaming new table...")
        cursor.execute('ALTER TABLE fundamentals_new RENAME TO fundamentals')
        log_message("[OK] Renamed to fundamentals")

        # Step 5: Recreate index
        log_message("[5/5] Recreating index...")
        cursor.execute('CREATE INDEX idx_fundamentals_symbol ON fundamentals(symbol)')
        log_message("[OK] Created index")

        conn.commit()
        log_message("")
        log_message("="*70)
        log_message("[SUCCESS] MIGRATION COMPLETE!")
        log_message("="*70)
        log_message("")
        log_message("Next steps:")
        log_message("  1. Run: python scripts/rebuild/06_rescrape_failed_stocks.py")
        log_message("  2. This will re-scrape the 80 failed stocks")
        log_message("  3. Should capture market_cap, current_price, etc. even with negative book_value")
        log_message("")

        return True

    except Exception as e:
        log_message(f"[ERROR] Migration failed: {e}")
        conn.rollback()
        import traceback
        traceback.print_exc()
        return False

    finally:
        conn.close()

if __name__ == "__main__":
    try:
        success = migrate_schema()
        exit(0 if success else 1)
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
