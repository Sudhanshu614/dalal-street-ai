"""
Custom Date Range Backfill Script (Optimized)
---------------------------------------------
Backfill OHLC data for a specific date range with EQ+BE series.
Optimized for speed by disabling ticker tracking overhead.

Usage:
    python BACKFILL_CUSTOM.py --start 2024-01-01 --end 2024-12-31
    python BACKFILL_CUSTOM.py --last-n-days 30
    python BACKFILL_CUSTOM.py --force  (to overwrite existing data)
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Add project root
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from src.data_fetcher.bhavcopy_downloader import BhavcopyDownloader

def main():
    parser = argparse.ArgumentParser(description="Backfill OHLC data for custom date range")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--last-n-days", type=int, help="Process last N days")
    parser.add_argument("--force", action="store_true", help="Force reprocess even if data exists")
    args = parser.parse_args()

    db_path = project_root / "database" / "stock_market_new.db"
    
    if not db_path.exists():
        print(f"[ERROR] Database not found at {db_path}")
        sys.exit(1)

    # Determine date range
    if args.last_n_days:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=args.last_n_days)
    elif args.start and args.end:
        try:
            start_date = datetime.strptime(args.start, '%Y-%m-%d')
            end_date = datetime.strptime(args.end, '%Y-%m-%d')
        except ValueError:
            print("[ERROR] Invalid date format. Use YYYY-MM-DD")
            sys.exit(1)
    else:
        print("[ERROR] Please specify --start and --end OR --last-n-days")
        parser.print_help()
        sys.exit(1)

    print("="*70)
    print("CUSTOM BACKFILL (OPTIMIZED)")
    print("="*70)
    print(f"[INFO] Start: {start_date.strftime('%Y-%m-%d')}")
    print(f"[INFO] End:   {end_date.strftime('%Y-%m-%d')}")
    print(f"[INFO] Force: {args.force}")
    print(f"[INFO] Mode:  Pure OHLC Scrape (Ticker Tracking Disabled)")
    
    # Initialize downloader with optimizations enabled
    downloader = BhavcopyDownloader(
        str(db_path),
        enable_ipo_detection=False,
        enable_demerger_correlation=False,
        enable_ticker_tracking=False  # Speed optimization
    )
    
    # Run backfill
    result = downloader.backfill_date_range(
        start_date=start_date,
        end_date=end_date,
        force=args.force,
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
