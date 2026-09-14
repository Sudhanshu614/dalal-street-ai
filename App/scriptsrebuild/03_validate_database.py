"""
PHASE 3: VALIDATE DATABASE QUALITY
Checks for data quality issues

Validations:
- No NULL company names
- No negative prices
- No symbol-as-name bugs
- Coverage statistics
- Data range checks

Usage:
    python scripts/rebuild/03_validate_database.py
"""

import sqlite3
from pathlib import Path
from datetime import datetime

DB_FILE = Path(__file__).parent.parent.parent / "database" / "stock_market_new.db"

def log_message(message):
    print(message)

def validate_database():
    log_message("="*70)
    log_message("DATABASE VALIDATION")
    log_message("="*70)
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found")
        return False

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    all_passed = True

    # ========================================================================
    # TEST 1: Stock Master Quality
    # ========================================================================
    log_message("[TEST 1] Stock Master Quality")
    log_message("-"*70)

    # Count total stocks
    cursor.execute("SELECT COUNT(*) FROM stocks_master")
    total_stocks = cursor.fetchone()[0]
    log_message(f"Total stocks: {total_stocks}")

    # Check for NULL names
    cursor.execute("SELECT COUNT(*) FROM stocks_master WHERE company_name IS NULL OR company_name = ''")
    null_names = cursor.fetchone()[0]

    if null_names == 0:
        log_message(f"[OK] NULL company names: {null_names}")
    else:
        log_message(f"[FAIL] NULL company names: {null_names}")
        all_passed = False

    # Check for symbol-as-name bugs
    cursor.execute("SELECT COUNT(*) FROM stocks_master WHERE company_name = symbol")
    symbol_as_name = cursor.fetchone()[0]

    if symbol_as_name == 0:
        log_message(f"[OK] Symbol-as-name bugs: {symbol_as_name}")
    else:
        log_message(f"[FAIL] Symbol-as-name bugs: {symbol_as_name}")
        all_passed = False

    log_message("")

    # ========================================================================
    # TEST 2: OHLCV Quality
    # ========================================================================
    log_message("[TEST 2] OHLCV Data Quality")
    log_message("-"*70)

    # Count total records
    cursor.execute("SELECT COUNT(*) FROM daily_ohlc")
    total_ohlc = cursor.fetchone()[0]
    log_message(f"Total OHLC records: {total_ohlc:,}")

    # Check for negative prices (should be ZERO due to CHECK constraints)
    cursor.execute("""
        SELECT COUNT(*) FROM daily_ohlc
        WHERE open < 0 OR high < 0 OR low < 0 OR close < 0
    """)
    negative_prices = cursor.fetchone()[0]

    if negative_prices == 0:
        log_message(f"[OK] Negative prices: {negative_prices}")
    else:
        log_message(f"[FAIL] Negative prices: {negative_prices} (CHECK constraint failed!)")
        all_passed = False

    # Check for invalid ranges (high < low)
    cursor.execute("SELECT COUNT(*) FROM daily_ohlc WHERE high < low")
    invalid_ranges = cursor.fetchone()[0]

    if invalid_ranges == 0:
        log_message(f"[OK] Invalid ranges (high < low): {invalid_ranges}")
    else:
        log_message(f"[FAIL] Invalid ranges: {invalid_ranges}")
        all_passed = False

    # Date range
    cursor.execute("SELECT MIN(date), MAX(date) FROM daily_ohlc")
    min_date, max_date = cursor.fetchone()
    log_message(f"Date range: {min_date} to {max_date}")

    # Coverage per stock
    cursor.execute("""
        SELECT symbol, COUNT(*) as record_count
        FROM daily_ohlc
        GROUP BY symbol
        ORDER BY record_count DESC
        LIMIT 5
    """)
    top_stocks = cursor.fetchall()
    log_message("Top 5 stocks by record count:")
    for symbol, count in top_stocks:
        log_message(f"  {symbol}: {count:,} records")

    log_message("")

    # ========================================================================
    # TEST 3: Fundamentals Coverage
    # ========================================================================
    log_message("[TEST 3] Fundamentals Coverage")
    log_message("-"*70)

    cursor.execute("SELECT COUNT(*) FROM fundamentals")
    total_fundamentals = cursor.fetchone()[0]
    log_message(f"Total stocks with fundamentals: {total_fundamentals}")

    # Coverage by field
    fields = [
        ('market_cap', 'Market Cap'),
        ('pe_ratio', 'PE Ratio'),
        ('pb_ratio', 'PB Ratio'),
        ('book_value', 'Book Value'),
        ('roe', 'ROE'),
        ('roce', 'ROCE'),
        ('promoter_holding', 'Promoter Holding'),
    ]

    for field, name in fields:
        cursor.execute(f"SELECT COUNT(*) FROM fundamentals WHERE {field} IS NOT NULL")
        count = cursor.fetchone()[0]
        coverage = (count / total_fundamentals * 100) if total_fundamentals > 0 else 0
        log_message(f"  {name}: {count}/{total_fundamentals} ({coverage:.1f}%)")

    log_message("")

    # ========================================================================
    # TEST 4: Corporate Actions
    # ========================================================================
    log_message("[TEST 4] Corporate Actions")
    log_message("-"*70)

    cursor.execute("SELECT COUNT(*) FROM corporate_actions")
    total_actions = cursor.fetchone()[0]
    log_message(f"Total corporate actions: {total_actions:,}")

    if total_actions > 0:
        cursor.execute("SELECT action_type, COUNT(*) FROM corporate_actions GROUP BY action_type ORDER BY COUNT(*) DESC LIMIT 5")
        action_types = cursor.fetchall()
        log_message("Top 5 action types:")
        for action_type, count in action_types:
            log_message(f"  {action_type}: {count:,}")
    else:
        log_message("[WARN] No corporate actions found")

    log_message("")

    # ========================================================================
    # TEST 5: Database Size & Performance
    # ========================================================================
    log_message("[TEST 5] Database Statistics")
    log_message("-"*70)

    size_mb = DB_FILE.stat().st_size / (1024 * 1024)
    log_message(f"Database size: {size_mb:.2f} MB")

    # Test query performance
    import time
    start = time.perf_counter()
    cursor.execute("SELECT * FROM stocks_master WHERE symbol = 'RELIANCE'")
    elapsed = (time.perf_counter() - start) * 1000
    log_message(f"Query performance: {elapsed:.2f}ms")

    log_message("")

    # ========================================================================
    # SUMMARY
    # ========================================================================
    log_message("="*70)
    if all_passed:
        log_message("[SUCCESS] ALL VALIDATIONS PASSED")
    else:
        log_message("[FAILED] SOME VALIDATIONS FAILED")
    log_message("="*70)
    log_message("")

    if all_passed:
        log_message("Database is production-ready!")
        log_message("")
        log_message("Summary:")
        log_message(f"  - {total_stocks} stocks")
        log_message(f"  - {total_ohlc:,} OHLC records")
        log_message(f"  - {total_fundamentals} fundamental records")
        log_message(f"  - {total_actions:,} corporate actions")
        log_message(f"  - {size_mb:.2f} MB database")
        log_message("")
        log_message("Next steps:")
        log_message("  1. Upload BSE Excel for stock_aliases")
        log_message("  2. Run 04_process_stock_aliases.py")
        log_message("  3. Rename stock_market_new.db → stock_market.db")
        log_message("  4. Update backend to use new database")
    else:
        log_message("Issues found - review output above")

    conn.close()

    return all_passed

if __name__ == "__main__":
    try:
        success = validate_database()
        exit(0 if success else 1)
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
