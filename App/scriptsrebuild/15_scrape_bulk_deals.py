"""
PHASE 15: SCRAPE BULK DEALS DATA
Scrapes bulk deal transactions from NSE

Data Source: NSE Archives CSV files
URL: https://archives.nseindia.com/content/equities/bulk.csv

Fields captured:
- Date
- Symbol
- Security Name
- Client Name
- Buy/Sell
- Quantity Traded
- Trade Price / Weighted Avg Price

Creates new table: bulk_deals

Usage:
    python scripts/rebuild/15_scrape_bulk_deals.py --start-date 2020-01-01 --end-date 2025-10-09
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import requests
import csv
from io import StringIO
import argparse

DB_FILE = Path(__file__).parent.parent.parent / "database" / "stock_market_new.db"
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / f"15_bulk_deals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log_message(message):
    """Log to console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)

    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry + '\n')

def create_bulk_deals_table():
    """Create bulk deals table if not exists"""
    log_message("="*70)
    log_message("CREATE BULK DEALS TABLE")
    log_message("="*70)
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found")
        return False

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bulk_deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                security_name TEXT,
                client_name TEXT,
                deal_type TEXT,
                quantity REAL,
                trade_price REAL,
                remarks TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(date, symbol, client_name, deal_type, quantity)
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_bulk_deals_date ON bulk_deals(date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_bulk_deals_symbol ON bulk_deals(symbol)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_bulk_deals_client ON bulk_deals(client_name)')

        conn.commit()
        log_message("[OK] Bulk deals table created")
        log_message("")

        return True

    except Exception as e:
        log_message(f"[ERROR] Table creation failed: {e}")
        return False

    finally:
        conn.close()

def map_symbol(cursor, raw_symbol):
    """Map NSE symbol to stocks_master symbol"""
    try:
        # Direct lookup
        cursor.execute("SELECT symbol FROM stocks_master WHERE symbol = ?", (raw_symbol,))
        row = cursor.fetchone()
        if row:
            return row[0]

        # Try without suffix
        base_symbol = raw_symbol.split('-')[0]
        cursor.execute("SELECT symbol FROM stocks_master WHERE symbol = ?", (base_symbol,))
        row = cursor.fetchone()
        if row:
            return row[0]
    except:
        # Table doesn't exist yet, just return raw symbol
        pass

    return raw_symbol

def fetch_bulk_deals_for_date(date_str):
    """
    Fetch bulk deals for a specific date from NSE CSV

    Args:
        date_str: Date in YYYY-MM-DD format

    Returns:
        list of bulk deal dicts or None if error
    """
    url = "https://archives.nseindia.com/content/equities/bulk.csv"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            return None, f"HTTP_{response.status_code}"

        # Parse CSV
        csv_content = response.text
        reader = csv.DictReader(StringIO(csv_content))

        deals = []
        target_date_obj = datetime.strptime(date_str, '%Y-%m-%d')

        for row in reader:
            # Parse date
            date_field = row.get('Date', '').strip()
            if not date_field:
                continue

            try:
                # Parse DD-MMM-YYYY format (e.g., "08-OCT-2025")
                row_date = datetime.strptime(date_field, '%d-%b-%Y')

                # Only include deals from target date
                if row_date.strftime('%Y-%m-%d') != date_str:
                    continue

                symbol = row.get('Symbol', '').strip()
                security_name = row.get('Security Name', '').strip()
                client_name = row.get('Client Name', '').strip()
                deal_type = row.get('Buy/Sell', '').strip()
                quantity_str = row.get('Quantity Traded', '').replace(',', '').strip()
                price_str = row.get('Trade Price / Wght. Avg. Price', '').replace(',', '').strip()
                remarks = row.get('Remarks', '').strip()

                quantity = float(quantity_str) if quantity_str else None
                price = float(price_str) if price_str else None

                deals.append({
                    'date': date_str,
                    'symbol': symbol,
                    'security_name': security_name,
                    'client_name': client_name,
                    'deal_type': deal_type,
                    'quantity': quantity,
                    'trade_price': price,
                    'remarks': remarks
                })

            except Exception as e:
                continue

        return deals, None

    except Exception as e:
        return None, str(e)

def scrape_bulk_deals(start_date, end_date):
    """Scrape bulk deals for date range"""
    log_message("="*70)
    log_message("SCRAPE BULK DEALS DATA")
    log_message("="*70)
    log_message("")

    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')

    total_days = (end_dt - start_dt).days + 1

    log_message(f"Date range: {start_date} to {end_date}")
    log_message(f"Total days: {total_days}")
    log_message("")
    log_message("NOTE: This scrapes from the current NSE bulk.csv file,")
    log_message("      which typically contains only the most recent data.")
    log_message("      For historical data, you would need daily snapshots.")
    log_message("")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    total_deals = 0
    total_inserted = 0
    total_skipped = 0
    failed_count = 0

    try:
        current_dt = start_dt

        while current_dt <= end_dt:
            date_str = current_dt.strftime('%Y-%m-%d')
            day_name = current_dt.strftime('%a')

            # Skip weekends
            if day_name in ['Sat', 'Sun']:
                current_dt += timedelta(days=1)
                continue

            log_message(f"[{date_str} {day_name}] Fetching...")

            # Fetch deals
            deals, error = fetch_bulk_deals_for_date(date_str)

            if error:
                log_message(f"  [FAIL] {error}")
                failed_count += 1
                current_dt += timedelta(days=1)
                continue

            if not deals:
                log_message(f"  [SKIP] No deals found")
                current_dt += timedelta(days=1)
                continue

            # Insert deals
            deals_inserted = 0
            deals_skipped = 0

            for deal in deals:
                # Map symbol
                mapped_symbol = map_symbol(cursor, deal['symbol'])

                try:
                    cursor.execute('''
                        INSERT OR IGNORE INTO bulk_deals
                        (date, symbol, security_name, client_name, deal_type, quantity, trade_price, remarks)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        deal['date'],
                        mapped_symbol,
                        deal['security_name'],
                        deal['client_name'],
                        deal['deal_type'],
                        deal['quantity'],
                        deal['trade_price'],
                        deal['remarks']
                    ))

                    if cursor.rowcount > 0:
                        deals_inserted += 1
                    else:
                        deals_skipped += 1

                except Exception as e:
                    log_message(f"  [FAIL] Insert error for {deal['symbol']}: {e}")
                    continue

            conn.commit()

            total_deals += len(deals)
            total_inserted += deals_inserted
            total_skipped += deals_skipped

            log_message(f"  [OK] Found {len(deals)} deals, Inserted: {deals_inserted}, Skipped: {deals_skipped}")

            current_dt += timedelta(days=1)

        log_message("")
        log_message("="*70)
        log_message("SUMMARY")
        log_message("="*70)
        log_message(f"Total days processed: {total_days}")
        log_message(f"Total deals found: {total_deals}")
        log_message(f"Inserted: {total_inserted}")
        log_message(f"Skipped (duplicates): {total_skipped}")
        log_message(f"Failed: {failed_count}")
        log_message("")
        log_message(f"[SUCCESS] Bulk deals scraping complete!")
        log_message(f"Log file: {LOG_FILE}")
        log_message("")

        return True

    except Exception as e:
        log_message(f"[ERROR] Scraping failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Scrape bulk deals from NSE')
    parser.add_argument('--start-date', type=str, default=datetime.now().strftime('%Y-%m-%d'), help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str, default=datetime.now().strftime('%Y-%m-%d'), help='End date (YYYY-MM-DD)')

    args = parser.parse_args()

    try:
        # Step 1: Create table
        if not create_bulk_deals_table():
            exit(1)

        # Step 2: Scrape data
        success = scrape_bulk_deals(args.start_date, args.end_date)
        exit(0 if success else 1)

    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
