"""
Production-Ready BE Series Backfill Script
-------------------------------------------
Add BE (Book Entry) series data to existing dates WITHOUT deleting EQ data.
Uses INSERT OR IGNORE strategy - existing EQ records are skipped, only new BE records are added.

This will process 6,576 dates from 1995-02-08 to 2025-11-25.
Estimated time: 1-2 hours depending on your system.

Usage:
    python BACKFILL_BE_SERIES_FULL.py
"""
import sys
import sqlite3
import time
from pathlib import Path
from datetime import datetime

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
    db_path = project_root / "database" / "stock_market_new.db"
    
    if not db_path.exists():
        print(f"[ERROR] Database not found at {db_path}")
        sys.exit(1)

    print("="*70)
    print("BE SERIES FULL HISTORICAL BACKFILL")
    print("="*70)

    # Get all existing dates
    existing_dates = get_existing_dates(str(db_path))
    total_dates = len(existing_dates)
    
    print(f"\n[INFO] Found {total_dates} dates with existing data")
    print(f"[INFO] Range: {existing_dates[0]} to {existing_dates[-1]}")
    print(f"\n[STRATEGY] Add BE records WITHOUT deleting existing EQ data")
    print(f"[STRATEGY] Existing records will be skipped (no duplicates)")
    print(f"\n[WARNING] This will process ALL {total_dates} dates!")
    print(f"[WARNING] Estimated time: 1-2 hours")
    
    response = input("\nProceed with full backfill? (yes/no): ")
    if response.lower() != 'yes':
        print("[ABORT] User cancelled")
        sys.exit(0)
    
    success = 0
    failed = 0
    be_records_added = 0
    
    start_time = time.time()
    
    for i, date_str in enumerate(existing_dates, 1):
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            
            # Progress indicator every 100 dates
            if i % 100 == 0 or i == 1:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                remaining = (total_dates - i) / rate if rate > 0 else 0
                print(f"\n[PROGRESS] {i}/{total_dates} ({i/total_dates*100:.1f}%) | "
                      f"Speed: {rate:.1f}/sec | ETA: {remaining/60:.0f} min | "
                      f"BE added so far: {be_records_added}")
            
            print(f"  [{i}/{total_dates}] {date_str}...", end=" ", flush=True)
            
            # Count before
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM daily_ohlc WHERE date = ?", (date_str,))
            before = cursor.fetchone()[0]
            conn.close()
            
            # Create fresh downloader instance for each date (clean connection)
            downloader = BhavcopyDownloader(str(db_path))
            
            # Process - will skip existing EQ, add new BE
            result = downloader.update_daily(date=date_obj)
            
            # Close the downloader's connection explicitly
            downloader.conn.close()
            
            # Count after
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM daily_ohlc WHERE date = ?", (date_str,))
            after = cursor.fetchone()[0]
            conn.close()
            
            improvement = after - before
            be_records_added += improvement
            
            if result.get('status') in ['success', 'already_loaded']:
                success += 1
                print(f"✓ ({after} total, +{improvement} BE)")
            else:
                failed += 1
                print(f"✗ Failed")
                
        except Exception as e:
            failed += 1
            print(f"✗ Error: {str(e)[:50]}")
    
    elapsed_total = time.time() - start_time
    
    print("\n" + "="*70)
    print("BACKFILL COMPLETE")
    print("="*70)
    print(f"  ✓ Success: {success}/{total_dates}")
    print(f"  ✗ Failed: {failed}/{total_dates}")
    print(f"  📈 Total BE records added: {be_records_added:,}")
    print(f"  ⏱️  Total time: {elapsed_total/60:.1f} minutes")
    print(f"  ⚡ Average speed: {total_dates/elapsed_total:.2f} dates/sec")

if __name__ == "__main__":
    main()
