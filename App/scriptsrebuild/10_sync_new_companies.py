"""
NEW COMPANY SYNC SCRIPT (Simplified)
Syncs companies from stocks_master to fundamentals table without scraping.

Purpose:
- Detects gap between stocks_master and fundamentals
- Inserts placeholder rows for new companies
- Relies on update_fundamentals.py to fetch actual data later
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import argparse
import sys

# Database path (relative to script location)
SCRIPT_DIR = Path(__file__).resolve().parent
DB_FILE = SCRIPT_DIR.parent / 'database' / 'stock_market_new.db'

def log_message(message):
    """Log to console with timestamp."""
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] {message}")

def find_gap(conn):
    """Find companies in stocks_master but missing from fundamentals."""
    cursor = conn.cursor()
    
    # Select symbol and company_name for active stocks not in fundamentals
    cursor.execute("""
        SELECT sm.symbol, sm.company_name
        FROM stocks_master sm
        LEFT JOIN fundamentals f ON sm.symbol = f.symbol
        WHERE sm.is_active = 1 AND f.symbol IS NULL
        ORDER BY sm.symbol
    """)
    return cursor.fetchall()

def insert_placeholder_company(conn, symbol, company_name):
    """Insert a placeholder row for a new company."""
    cursor = conn.cursor()
    
    # Insert minimal data. 
    # update_fundamentals.py will be responsible for filling in the rest.
    cursor.execute('''
        INSERT INTO fundamentals
        (symbol, company_name, data_source, created_at, last_updated)
        VALUES (?, ?, 'sync_script', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ''', (symbol, company_name))

def main():
    parser = argparse.ArgumentParser(
        description='Sync new companies from stocks_master to fundamentals (Structure Only)'
    )
    parser.add_argument('--limit', type=int, default=0, help='Limit number of rows to sync')
    args = parser.parse_args()

    log_message("="*70)
    log_message("NEW COMPANY SYNC - Structural Sync Only")
    log_message("="*70)

    if not DB_FILE.exists():
        log_message(f"[ERROR] Database not found at {DB_FILE}")
        return False

    try:
        conn = sqlite3.connect(DB_FILE)
    except sqlite3.Error as e:
        log_message(f"[ERROR] Failed to connect to database: {e}")
        return False
    
    # Find gap
    try:
        gap = find_gap(conn)
    except Exception as e:
        log_message(f"[ERROR] Failed to query database: {e}")
        conn.close()
        return False
    
    if not gap:
        log_message("[INFO] No missing companies found - database is in sync!")
        conn.close()
        return True
    
    # Apply limit
    if args.limit and args.limit > 0:
        gap = gap[:args.limit]
    
    total = len(gap)
    log_message(f"Found {total} companies missing from fundamentals.")
    log_message("Syncing structural rows (no scraping)...")
    log_message("-" * 70)
    
    success_count = 0
    failed_count = 0
    
    for i, (symbol, name) in enumerate(gap, 1):
        try:
            insert_placeholder_company(conn, symbol, name)
            conn.commit()
            
            # Clean output
            print(f"[{i}/{total}] {symbol:<15} [OK] Added")
            success_count += 1
            
        except Exception as e:
            print(f"[{i}/{total}] {symbol:<15} [FAIL] {e}")
            failed_count += 1
    
    conn.close()
    
    log_message("-" * 70)
    log_message("SUMMARY")
    log_message(f"Total Found: {total}")
    log_message(f"Added:       {success_count}")
    log_message(f"Failed:      {failed_count}")
    log_message("-" * 70)
    
    if success_count > 0:
        log_message("Next step: Run 'update_fundamentals.py' to populate data.")
    
    return True

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        sys.exit(1)
