"""
Daily Bhavcopy Runner
---------------------
Standard daily update script with full ticker tracking enabled.
Use this for regular daily updates to ensure IPOs, name changes, and metadata are captured.

Usage:
    python DAILY_RUNNER.py              # Run for TODAY
    python DAILY_RUNNER.py --date 2025-11-26
    python DAILY_RUNNER.py --start 2025-11-01 --end 2025-11-26
    python DAILY_RUNNER.py --force      # Overwrite existing data
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime

# Add project root
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from src.data_fetcher.bhavcopy_downloader import BhavcopyDownloader

def main():
    parser = argparse.ArgumentParser(description="Daily Bhavcopy Runner (Full Tracking)")
    parser.add_argument("--date", help="Specific date (YYYY-MM-DD)")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="Force reprocess even if data exists")
    args = parser.parse_args()

    db_path = project_root / "database" / "stock_market_new.db"
    
    if not db_path.exists():
        print(f"[ERROR] Database not found at {db_path}")
        sys.exit(1)

    # Initialize downloader with FULL TRACKING enabled (default)
    # This ensures we catch IPOs, name changes, etc.
    downloader = BhavcopyDownloader(
        str(db_path),
        enable_ipo_detection=True,
        enable_demerger_correlation=True,
        enable_ticker_tracking=True
    )

    print("="*70)
    print("DAILY BHAVCOPY RUNNER")
    print("="*70)
    print(f"[INFO] Tracking: ENABLED (IPOs, Name Changes, Metadata)")
    print(f"[INFO] Force:    {args.force}")

    # Mode 1: Date Range
    if args.start and args.end:
        start_date = datetime.strptime(args.start, '%Y-%m-%d')
        end_date = datetime.strptime(args.end, '%Y-%m-%d')
        print(f"[MODE] Range: {args.start} to {args.end}")
        
        downloader.backfill_date_range(
            start_date=start_date,
            end_date=end_date,
            force=args.force,
            skip_weekends=True
        )

    # Mode 2: Specific Date
    elif args.date:
        target_date = datetime.strptime(args.date, '%Y-%m-%d')
        print(f"[MODE] Specific Date: {args.date}")
        
        # Use backfill_date_range for single date to handle force logic consistently
        downloader.backfill_date_range(
            start_date=target_date,
            end_date=target_date,
            force=args.force
        )

    # Mode 3: Today (Default)
    else:
        today = datetime.now()
        print(f"[MODE] Today: {today.strftime('%Y-%m-%d')}")
        
        # Use update_daily directly for today to get detailed report
        try:
            # If force is requested, we need to handle deletion manually or use backfill
            if args.force:
                downloader.backfill_date_range(today, today, force=True)
            else:
                downloader.update_daily(today)
        except Exception as e:
            print(f"[ERROR] Failed: {e}")

if __name__ == "__main__":
    main()
