"""
MASTER DEPLOYMENT SCRIPT - STOCK MARKET DATABASE ENHANCEMENT
Single command to run complete database enhancement on VM

Phases included:
- Phase 5: Fix book value constraint
- Phase 6: Re-scrape failed stocks
- Phase 7: Add enhanced fields + create quarterly/annual tables
- Phase 8: Scrape enhanced fundamentals + quarterly + annual data
- Phase 9: Calculate returns from OHLC data
- Phase 10: Scrape market indices (2020-2025)
- Phase 11: Import IPO data from CSV files
- Phase 14: Scrape FII/DII data (2020-2025)

Total estimated time: 8-10 hours

Usage on VM:
    screen -S rebuild
    python scripts/rebuild/MASTER_DEPLOY.py

To detach: Ctrl+A then D
To reattach: screen -r rebuild
"""

import sys
import subprocess
from pathlib import Path
from datetime import datetime
import argparse

SCRIPT_DIR = Path(__file__).parent
DB_FILE = SCRIPT_DIR.parent.parent / "database" / "stock_market_new.db"
LOG_DIR = SCRIPT_DIR.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

MASTER_LOG = LOG_DIR / f"MASTER_DEPLOY_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

PHASES = [
    {
        'num': 5,
        'name': 'Fix Book Value Constraint',
        'script': '05_migrate_book_value_constraint.py',
        'time': '< 1 min',
        'description': 'Remove book_value > 0 constraint'
    },
    {
        'num': 6,
        'name': 'Re-scrape Failed Stocks',
        'script': '06_rescrape_failed_stocks.py',
        'time': '~10 min',
        'description': 'Re-scrape 80+ stocks that failed due to book value constraint'
    },
    {
        'num': 7,
        'name': 'Add Enhanced Fields',
        'script': '07_migrate_add_enhanced_fields.py',
        'time': '< 1 min',
        'description': 'Add 20 new fields + create quarterly/annual tables'
    },
    {
        'num': 8,
        'name': 'Scrape Enhanced Fundamentals',
        'script': '08_scrape_enhanced_fundamentals.py',
        'time': '~2-3 hours',
        'description': 'Scrape industry, growth, debt from Screener.in (2,103 stocks)'
    },
    {
        'num': 9,
        'name': 'Calculate Returns',
        'script': '09_calculate_returns.py',
        'time': '~10 min',
        'description': 'Calculate 1M, 3M, 6M, 1Y, 3Y, 5Y returns from OHLC data'
    },
    {
        'num': 10,
        'name': 'Scrape Market Indices',
        'script': '10_scrape_market_indices.py',
        'time': '~3-4 hours',
        'description': 'Scrape 143 indices from NSE (2020-2025, ~5 years)',
        'args': ['--start-date', '2020-01-01']
    },
    {
        'num': 11,
        'name': 'Import IPO Data',
        'script': '11_import_ipo_data.py',
        'time': '~5 min',
        'description': 'Import IPO data from 19 CSV files (2007-2025)'
    },
    {
        'num': 14,
        'name': 'Scrape FII/DII Data',
        'script': '14_scrape_fii_dii_data.py',
        'time': '~2-3 hours',
        'description': 'Scrape FII/DII data from NSE (2020-2025)',
        'args': ['--start-date', '2020-01-01']
    },
]

def log_message(message, also_print=True):
    """Log to file and optionally print"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"

    if also_print:
        print(log_entry)

    with open(MASTER_LOG, 'a', encoding='utf-8') as f:
        f.write(log_entry + '\n')

def print_banner(text):
    """Print a banner"""
    print()
    print("=" * 70)
    print(text.center(70))
    print("=" * 70)
    print()

def check_prerequisites():
    """Check if database and files exist"""
    log_message("Checking prerequisites...")

    if not DB_FILE.exists():
        log_message(f"[ERROR] Database not found: {DB_FILE}")
        return False

    log_message(f"OK Database found")

    # Check all phase scripts exist
    missing = []
    for phase in PHASES:
        script_path = SCRIPT_DIR / phase['script']
        if not script_path.exists():
            missing.append(f"Phase {phase['num']}: {phase['script']}")

    if missing:
        log_message("[ERROR] Missing scripts:")
        for m in missing:
            log_message(f"  - {m}")
        return False

    log_message("OK All phase scripts found")
    return True

def run_phase(phase):
    """Run a single phase"""
    print_banner(f"PHASE {phase['num']}: {phase['name']}")

    log_message(f"Description: {phase['description']}")
    log_message(f"Estimated time: {phase['time']}")
    log_message("")

    script_path = SCRIPT_DIR / phase['script']
    args = phase.get('args', [])

    start_time = datetime.now()
    log_message(f"Starting at {start_time.strftime('%H:%M:%S')}")

    try:
        cmd = [sys.executable, str(script_path)] + args
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')

        end_time = datetime.now()
        elapsed = end_time - start_time

        # Log output
        if result.stdout:
            log_message("--- OUTPUT ---", also_print=False)
            log_message(result.stdout, also_print=False)

        if result.stderr:
            log_message("--- ERRORS ---", also_print=False)
            log_message(result.stderr, also_print=False)

        if result.returncode == 0:
            log_message(f"OK Phase {phase['num']} completed in {elapsed}")
            log_message("")
            return True, elapsed.total_seconds()
        else:
            log_message(f"FAILED Phase {phase['num']} (exit code {result.returncode})")
            log_message("")
            return False, elapsed.total_seconds()

    except Exception as e:
        end_time = datetime.now()
        elapsed = end_time - start_time
        log_message(f"FAILED Phase {phase['num']}: {e}")
        log_message("")
        return False, elapsed.total_seconds()

def main():
    parser = argparse.ArgumentParser(description='Master deployment for database enhancement')
    parser.add_argument('--resume', type=int, help='Resume from specific phase number')
    parser.add_argument('--skip', type=int, help='Skip specific phase number')

    args = parser.parse_args()

    phases_to_run = PHASES

    if args.resume:
        phases_to_run = [p for p in PHASES if p['num'] >= args.resume]

    if args.skip:
        phases_to_run = [p for p in phases_to_run if p['num'] != args.skip]

    print_banner("STOCK MARKET DATABASE ENHANCEMENT")

    print("Configuration:")
    print(f"  Database: {DB_FILE}")
    print(f"  Phases: {', '.join([str(p['num']) for p in phases_to_run])}")
    print(f"  Log file: {MASTER_LOG}")
    print()

    log_message("="*70)
    log_message("MASTER DEPLOYMENT STARTED")
    log_message("="*70)
    log_message("")

    if not check_prerequisites():
        print("[ERROR] Prerequisites check failed")
        return 1

    print()
    print("Execution Plan:")
    for phase in phases_to_run:
        print(f"  Phase {phase['num']}: {phase['name']} ({phase['time']})")
    print()
    print("TOTAL ESTIMATED TIME: 8-10 hours")
    print("TIP: Run in screen session to prevent disconnection")
    print()

    response = input("Continue? (yes/no): ")
    if response.lower() != 'yes':
        log_message("[ABORT] User cancelled")
        return 0

    log_message("Deployment approved by user")
    log_message("")

    overall_start = datetime.now()
    results = {}

    for phase in phases_to_run:
        success, elapsed = run_phase(phase)
        results[phase['num']] = {'success': success, 'elapsed': elapsed}

        if not success:
            print()
            print(f"[ERROR] Phase {phase['num']} failed. Stopping deployment.")
            print(f"See log: {MASTER_LOG}")
            log_message("[ABORT] Deployment stopped due to failure")
            break

    overall_end = datetime.now()
    overall_elapsed = overall_end - overall_start

    print_banner("DEPLOYMENT SUMMARY")

    log_message("="*70)
    log_message("DEPLOYMENT SUMMARY")
    log_message("="*70)
    log_message("")

    for phase in phases_to_run:
        if phase['num'] in results:
            result = results[phase['num']]
            status = "SUCCESS" if result['success'] else "FAILED"
            elapsed_str = f"{result['elapsed']:.1f}s"
            summary = f"Phase {phase['num']}: {status} ({elapsed_str})"
            log_message(summary)
            print(f"  {summary}")

    log_message("")
    log_message(f"Total time: {overall_elapsed}")
    log_message(f"Log file: {MASTER_LOG}")
    log_message("")

    print()
    print(f"Total time: {overall_elapsed}")
    print(f"Log file: {MASTER_LOG}")
    print()

    all_success = all(r['success'] for r in results.values())

    if all_success:
        print("ALL PHASES COMPLETED SUCCESSFULLY!")
        log_message("DEPLOYMENT COMPLETE!")
        return 0
    else:
        print("Some phases failed. Check log for details.")
        log_message("DEPLOYMENT INCOMPLETE")
        return 1

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n[ABORT] Deployment interrupted by user")
        log_message("[ABORT] Interrupted by user (Ctrl+C)")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        log_message(f"[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
