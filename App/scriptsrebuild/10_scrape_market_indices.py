"""
PHASE 10: SCRAPE MARKET INDICES
Scrapes all NSE market indices historical data from NSE Archives

Data Source: NSE Archives CSV files
URL Pattern: https://archives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv

Indices included (123 total):
- NIFTY 50, NIFTY NEXT 50, NIFTY 100, NIFTY 200, NIFTY 500
- Sectoral: NIFTY BANK, NIFTY IT, NIFTY AUTO, NIFTY PHARMA, NIFTY FMCG, etc.
- Thematic: NIFTY MIDCAP, NIFTY SMALLCAP, etc.

Fields captured:
- Index Name
- Date
- Open, High, Low, Close
- Points Change, Change %
- Volume, Turnover
- P/E, P/B, Div Yield

Creates new table: market_indices

Usage:
    python scripts/rebuild/10_scrape_market_indices.py --start-date 2020-01-01 --end-date 2025-10-09
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import requests
import csv
from io import StringIO
import argparse

DB_FILE = Path(__file__).parent.parent.parent / "App" / "Database" / "stock_market_new.db"
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / f"10_market_indices_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log_message(message):
    """Log to console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)

    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry + '\n')

def create_market_indices_table():
    """Create market indices table if not exists"""
    log_message("="*70)
    log_message("CREATE MARKET INDICES TABLE")
    log_message("="*70)
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found")
        return False

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_indices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                index_name TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                points_change REAL,
                change_percent REAL,
                volume REAL,
                turnover REAL,
                pe_ratio REAL,
                pb_ratio REAL,
                div_yield REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(index_name, date)
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_indices_name ON market_indices(index_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_indices_date ON market_indices(date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_indices_name_date ON market_indices(index_name, date)')

        conn.commit()
        log_message("[OK] Market indices table created")
        log_message("")

        return True

    except Exception as e:
        log_message(f"[ERROR] Table creation failed: {e}")
        return False

    finally:
        conn.close()

def parse_number(text):
    """Parse number from text"""
    if not text or text.strip() == '' or text.strip() == '-':
        return None
    try:
        return float(text.replace(',', '').strip())
    except:
        return None

def fetch_indices_for_date(date_str):
    """
    Fetch all indices data for a specific date from NSE

    Args:
        date_str: Date in YYYY-MM-DD format

    Returns:
        list of index dicts or None if error
    """
    # Convert to DDMMYYYY format for URL
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    url_date = date_obj.strftime('%d%m%Y')

    url = f"https://archives.nseindia.com/content/indices/ind_close_all_{url_date}.csv"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 404:
            return None, None, "NOT_FOUND"

        if response.status_code != 200:
            return None, None, f"HTTP_{response.status_code}"

        # Parse CSV
        csv_content = response.text
        reader = csv.DictReader(StringIO(csv_content))

        indices_data = []
        headers = reader.fieldnames or []

        for row in reader:
            index_name = row.get('Index Name', '').strip()

            if not index_name:
                continue

            # Parse fields
            index_data = {
                'index_name': index_name,
                'date': date_str,
                'open': parse_number(row.get('Open Index Value', '')),
                'high': parse_number(row.get('High Index Value', '')),
                'low': parse_number(row.get('Low Index Value', '')),
                'close': parse_number(row.get('Closing Index Value', '')),
                'points_change': parse_number(row.get('Points Change', '')),
                'change_percent': parse_number(row.get('Change(%)', '')),
                'volume': parse_number(row.get('Volume', '')),
                'turnover': parse_number(row.get('Turnover (Rs. Cr.)', '')),
                'pe_ratio': parse_number(row.get('P/E', '')),
                'pb_ratio': parse_number(row.get('P/B', '')),
                'div_yield': parse_number(row.get('Div Yield', ''))
            }

            indices_data.append(index_data)

        return indices_data, headers, None

    except Exception as e:
        return None, None, str(e)

def scrape_market_indices(start_date, end_date):
    """Scrape market indices for date range"""
    log_message("="*70)
    log_message("SCRAPE MARKET INDICES DATA")
    log_message("="*70)
    log_message("")

    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')

    total_days = (end_dt - start_dt).days + 1

    log_message(f"Date range: {start_date} to {end_date}")
    log_message(f"Total days: {total_days}")
    log_message("")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    success_count = 0
    skipped_count = 0
    failed_count = 0
    not_found_count = 0
    total_indices_inserted = 0

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

            # Fetch indices data
            indices_data, headers, error = fetch_indices_for_date(date_str)

            if error:
                if error == "NOT_FOUND":
                    log_message(f"  [SKIP] No data (holiday)")
                    not_found_count += 1
                else:
                    log_message(f"  [FAIL] {error}")
                    failed_count += 1

                current_dt += timedelta(days=1)
                continue

            if not indices_data:
                log_message(f"  [FAIL] No indices data returned")
                failed_count += 1
                current_dt += timedelta(days=1)
                continue

            # Log headers for clarity
            if headers:
                try:
                    log_message(f"  [INFO] CSV columns: {', '.join(headers)}")
                except Exception:
                    log_message("  [INFO] CSV columns present (non-ASCII)")

            # Insert indices into database with detailed reason codes
            indices_inserted = 0
            indices_duplicates = 0
            indices_skipped_no_name = 0
            insert_errors = 0

            # Preload existing names for date to classify duplicates precisely
            try:
                cursor.execute("SELECT index_name FROM market_indices WHERE date = ?", (date_str,))
                existing_names = {row[0] for row in cursor.fetchall()}
            except Exception:
                existing_names = set()

            for index_data in indices_data:
                try:
                    name = (index_data.get('index_name') or '').strip()
                    if not name:
                        indices_skipped_no_name += 1
                        log_message("    [SKIP] Row without index name")
                        continue

                    if name in existing_names:
                        indices_duplicates += 1
                        log_message(f"    [DUP] {name} already present for {date_str}")
                        continue

                    cursor.execute('''
                        INSERT INTO market_indices
                        (index_name, date, open, high, low, close, points_change, change_percent,
                         volume, turnover, pe_ratio, pb_ratio, div_yield)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        name,
                        index_data['date'],
                        index_data['open'],
                        index_data['high'],
                        index_data['low'],
                        index_data['close'],
                        index_data['points_change'],
                        index_data['change_percent'],
                        index_data['volume'],
                        index_data['turnover'],
                        index_data['pe_ratio'],
                        index_data['pb_ratio'],
                        index_data['div_yield']
                    ))

                    indices_inserted += 1
                    existing_names.add(name)

                except Exception as e:
                    insert_errors += 1
                    log_message(f"    [FAIL] Insert error for {index_data.get('index_name')}: {e}")
                    continue

            conn.commit()

            total_indices_inserted += indices_inserted

            log_message(f"  [OK] CSV rows: {len(indices_data)} | Inserted: {indices_inserted} | Duplicates: {indices_duplicates} | NoName: {indices_skipped_no_name} | Errors: {insert_errors}")
            success_count += 1

            current_dt += timedelta(days=1)

        log_message("")
        log_message("="*70)
        log_message("SUMMARY")
        log_message("="*70)
        log_message(f"Total days processed: {total_days}")
        log_message(f"Success: {success_count}")
        log_message(f"Not found (holiday): {not_found_count}")
        log_message(f"Failed: {failed_count}")
        log_message(f"Total indices records inserted: {total_indices_inserted:,}")
        log_message("")

        # Show unique indices count
        cursor.execute("SELECT COUNT(DISTINCT index_name) FROM market_indices")
        unique_indices = cursor.fetchone()[0]
        log_message(f"Unique indices tracked: {unique_indices}")

        log_message("")
        log_message(f"[SUCCESS] Market indices scraping complete!")
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
    parser = argparse.ArgumentParser(description='Scrape market indices from NSE')
    parser.add_argument('--start-date', type=str, default='2020-01-01', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str, default=datetime.now().strftime('%Y-%m-%d'), help='End date (YYYY-MM-DD)')

    args = parser.parse_args()

    try:
        # Step 1: Create table
        if not create_market_indices_table():
            exit(1)

        # Step 2: Scrape data
        success = scrape_market_indices(args.start_date, args.end_date)
        exit(0 if success else 1)

    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
