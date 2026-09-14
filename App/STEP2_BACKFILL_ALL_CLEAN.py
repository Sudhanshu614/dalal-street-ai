"""
STEP 2: Backfill All Dates with EQ+BE Series
---------------------------------------------
Run this AFTER deleting all OHLC data.
Since the table is empty, no duplicate checks needed - blazing fast!

This will process 6,576 dates from 1995-02-08 to 2025-11-25.
Estimated time: 1-2 hours.
"""
import sys
from pathlib import Path
from datetime import datetime

project_root = Path(__file__).parent
sys.path.append(str(project_root))

from src.data_fetcher.bhavcopy_downloader import BhavcopyDownloader

def main():
    db_path = project_root / "database" / "stock_market_new.db"
    
    print("="*70)
    print("BACKFILL ALL DATES (EQ + BE SERIES)")
    print("="*70)
    
    start_date = datetime(1995, 2, 8)
    end_date = datetime(2025, 11, 25)
    
    print(f"\n[INFO] Start: {start_date.strftime('%Y-%m-%d')}")
    print(f"[INFO] End: {end_date.strftime('%Y-%m-%d')}")
    print(f"[INFO] Strategy: Fresh insert (no duplicates)")
    print(f"[WARNING] Estimated time: 1-2 hours")
    
    response = input("\nProceed? (yes/no): ")
    if response.lower() != 'yes':
        print("[ABORT] User cancelled")
        sys.exit(0)
    
    # Use backfill WITHOUT force (since table is empty)
    # Disable optional tracking for speed
    downloader = BhavcopyDownloader(
        str(db_path),
        enable_ipo_detection=False,
        enable_demerger_correlation=False,
        enable_ticker_tracking=False
    )
    
    result = downloader.backfill_date_range(
        start_date=start_date,
        end_date=end_date,
        force=False,  # No force needed - table is empty!
        skip_weekends=True
    )
    
    print("\n" + "="*70)
    print("BACKFILL COMPLETE")
    print("="*70)
    print(f"  Total dates: {result['total_dates']}")
    print(f"  ✓ Success: {result['success']}")
    print(f"  ⊘ Skipped: {result['skipped']}")
    print(f"  ✗ Failed: {result['failed']}")

if __name__ == "__main__":
    main()
