"""
MASTER SCRIPT: ENHANCE DATABASE WITH NEW FIELDS
Runs all enhancement phases in order

This script:
1. Migrates database to add new fields (07)
2. Scrapes enhanced fundamentals from Screener.in (08)
3. Calculates returns from OHLCV data (09)
4. Scrapes IPO data (10)

Usage:
    python scripts/rebuild/MASTER_ENHANCE_DATABASE.py
"""

import subprocess
import sys
from pathlib import Path
from datetime import datetime

SCRIPTS_DIR = Path(__file__).parent

def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def run_script(script_name):
    """Run a Python script and return success status"""
    script_path = SCRIPTS_DIR / script_name

    log_message("="*70)
    log_message(f"RUNNING: {script_name}")
    log_message("="*70)
    log_message("")

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=False,
            text=True
        )

        if result.returncode == 0:
            log_message("")
            log_message(f"[SUCCESS] {script_name} completed")
            return True
        else:
            log_message("")
            log_message(f"[FAILED] {script_name} failed with exit code {result.returncode}")
            return False

    except Exception as e:
        log_message(f"[ERROR] Failed to run {script_name}: {e}")
        return False

def main():
    log_message("="*70)
    log_message("MASTER ENHANCEMENT SCRIPT")
    log_message("="*70)
    log_message("")
    log_message("This will enhance your database with:")
    log_message("  - Industry/Sector classification")
    log_message("  - Growth metrics (Sales, Profit, EPS)")
    log_message("  - Returns data (1M, 3M, 6M, 1Y, 3Y, 5Y)")
    log_message("  - Debt to Equity ratios")
    log_message("  - Promoter pledge percentages")
    log_message("  - IPO data (listing date, issue price, returns)")
    log_message("")
    log_message(f"Database: database/stock_market_new.db")
    log_message("")

    input("Press Enter to continue or Ctrl+C to cancel...")
    log_message("")

    start_time = datetime.now()

    # Phase 7: Migration (add new fields)
    log_message("PHASE 7: ADD NEW FIELDS TO FUNDAMENTALS TABLE")
    if not run_script("07_migrate_add_enhanced_fields.py"):
        log_message("[ABORT] Migration failed")
        return False

    log_message("")

    # Phase 8: Scrape enhanced fundamentals
    log_message("PHASE 8: SCRAPE ENHANCED FUNDAMENTALS")
    log_message("[INFO] This will take ~70 minutes (2,103 stocks × 2s delay)")
    log_message("")
    if not run_script("08_scrape_enhanced_fundamentals.py"):
        log_message("[ABORT] Enhanced fundamentals scraping failed")
        return False

    log_message("")

    # Phase 9: Calculate returns
    log_message("PHASE 9: CALCULATE RETURNS FROM OHLCV")
    log_message("[INFO] This will take ~2 minutes")
    log_message("")
    if not run_script("09_calculate_returns.py"):
        log_message("[ABORT] Returns calculation failed")
        return False

    log_message("")

    # Phase 10: Scrape IPO data
    log_message("PHASE 10: SCRAPE IPO DATA")
    log_message("[INFO] This will take ~10 seconds")
    log_message("")
    if not run_script("10_scrape_ipo_data.py"):
        log_message("[ABORT] IPO data scraping failed")
        return False

    # Final summary
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds() / 60

    log_message("")
    log_message("="*70)
    log_message("ALL ENHANCEMENTS COMPLETE!")
    log_message("="*70)
    log_message(f"Total time: {duration:.1f} minutes")
    log_message("")
    log_message("Your database now has:")
    log_message("  ✅ Industry/Sector classification for all stocks")
    log_message("  ✅ Growth metrics (Sales, Profit CAGR)")
    log_message("  ✅ Returns data (1M to 5Y)")
    log_message("  ✅ Debt to Equity ratios")
    log_message("  ✅ IPO data for 900+ companies")
    log_message("")
    log_message("Next: Run validation to check data quality")
    log_message("  python scripts/rebuild/03_validate_database.py")
    log_message("")

    return True

if __name__ == "__main__":
    try:
        success = main()
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n[CANCELLED] Enhancement cancelled by user")
        exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
