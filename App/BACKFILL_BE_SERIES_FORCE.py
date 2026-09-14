"""
Production BE Series Backfill - CORRECTED VERSION
--------------------------------------------------
Uses the backfill_date_range method with force=True to reprocess all dates.

This will process 6,576 dates from 1995-02-08 to 2025-11-25.
Estimated time: 1-2 hours depending on your system.

Usage:
    python BACKFILL_BE_SERIES_FORCE.py
"""
import sys
from pathlib import Path
from datetime import datetime

# Add project root
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from src.data_fetcher.bhavcopy_downloader import BhavcopyDownloader

def main():
    db_path = project_root / "database" / "stock_market_new.db"
    
    if not db_path.exists():
        print(f"[ERROR] Database not found at {db_path}")
        sys.exit(1)

    print("="*70)
    print("BE SERIES BACKFILL - FORCE MODE")
    print("="*70)
    
    # Date range
    start_date = datetime(1995, 2, 8)
    end_date = datetime(2025, 11, 25)
    
    total_days = (end_date - start_date).days
    
    print(f"\n[INFO] Start: {start_date.strftime('%Y-%m-%d')}")
    print(f"[INFO] End: {end_date.strftime('%Y-%m-%d')}")
    print(f"[INFO] Date range: {total_days} days")
    print(f"\n[STRATEGY] Force reprocess to add BE series")
    print(f"[WARNING] This will DELETE and REINSERT data for each date")
    print(f"[WARNING] Estimated time: 1-2 hours")
    
    response = input("\nProceed with FORCE backfill? (yes/no): ")
    if response.lower() != 'yes':
        print("[ABORT] User cancelled")
        sys.exit(0)
    
    # Use the built-in backfill_date_range method with force=True
    downloader = BhavcopyDownloader(str(db_path))
    
    result = downloader.backfill_date_range(
        start_date=start_date,
        end_date=end_date,
        force=True,  # This forces reprocessing even if data exists
        skip_weekends=True
    )
    
    # Print summary
    print("\n" + "="*70)
    print("BACKFILL COMPLETE")
    print("="*70)
    print(f"  Total dates attempted: {result['total_dates']}")
    print(f"  ✓ Success: {result['success']}")
    print(f"  ⊘ Skipped: {result['skipped']}")
    print(f"  ✗ Failed: {result['failed']}")

if __name__ == "__main__":
    main()
