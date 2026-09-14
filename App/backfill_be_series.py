"""
Historical BE Series Backfill Script
-------------------------------------
Reprocess all historical dates to add BE (Book Entry) series data
alongside existing EQ data.

Usage:
    python backfill_be_series.py --start 1995-02-08 --end 2025-11-25
    python backfill_be_series.py --last-n-days 30
"""
import sys
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Add project root
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from src.data_fetcher.bhavcopy_downloader import BhavcopyDownloader

def get_existing_dates(db_path: str):
    """Get all dates that already have OHLC data"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT date FROM daily_ohlc ORDER BY date")
    dates = [row[0] for row in cursor.fetchall()]
    conn.close()
    return dates

def main():
    parser = argparse.ArgumentParser(description="Backfill BE series for historical dates")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--last-n-days", type=int, help="Process last N days only")
    parser.add_argument("--force", action="store_true", help="Force reprocess even if data exists")
    args = parser.parse_args()

    db_path = project_root / "database" / "stock_market_new.db"
    
    if not db_path.exists():
        print(f"[ERROR] Database not found at {db_path}")
        sys.exit(1)

    print("="*70)
    print("BE SERIES HISTORICAL BACKFILL")
    print("="*70)

    # Determine date range
    if args.last_n_days:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=args.last_n_days)
        print(f"\n[MODE] Last {args.last_n_days} days")
    elif args.start and args.end:
        start_date = datetime.strptime(args.start, '%Y-%m-%d')
        end_date = datetime.strptime(args.end, '%Y-%m-%d')
        print(f"\n[MODE] Date range specified")
    else:
        # Default: process all existing dates
        print(f"\n[MODE] Processing all existing OHLC dates")
        existing_dates = get_existing_dates(str(db_path))
        print(f"[INFO] Found {len(existing_dates)} dates with existing data")
        print(f"[INFO] Range: {existing_dates[0]} to {existing_dates[-1]}")
        
        # Process each date
        downloader = BhavcopyDownloader(str(db_path))
        
        success = 0
        failed = 0
        skipped = 0
        
        for i, date_str in enumerate(existing_dates, 1):
            try:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                
                print(f"\n[{i}/{len(existing_dates)}] Processing {date_str}...")
                
                # Delete existing data for this date if force is set
                if args.force:
                    conn = sqlite3.connect(str(db_path))
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM daily_ohlc WHERE date = ?", (date_str,))
                    deleted = cursor.rowcount
                    conn.commit()
                    conn.close()
                    print(f"  [FORCE] Deleted {deleted} existing records")
                
                # Download and process
                result = downloader.update_daily(date=date_obj)
                
                if result.get('status') in ['success', 'already_loaded']:
                    success += 1
                    print(f"  ✓ Success: {result.get('ohlc_inserted', 0)} records")
                else:
                    failed += 1
                    print(f"  ✗ Failed")
                    
            except Exception as e:
                failed += 1
                print(f"  ✗ Error: {e}")
        
        print("\n" + "="*70)
        print("BACKFILL COMPLETE")
        print("="*70)
        print(f"  ✓ Success: {success}")
        print(f"  ✗ Failed: {failed}")
        print(f"  Total: {len(existing_dates)}")
        
        return

    # For specific date range (not used in default mode)
    print(f"[INFO] Start: {start_date.strftime('%Y-%m-%d')}")
    print(f"[INFO] End: {end_date.strftime('%Y-%m-%d')}")
    print(f"[INFO] Force reprocess: {args.force}")

if __name__ == "__main__":
    main()
