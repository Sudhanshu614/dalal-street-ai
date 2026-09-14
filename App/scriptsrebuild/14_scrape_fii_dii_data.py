"""
PHASE 14: SCRAPE FII/DII DATA
Scrapes Foreign Institutional Investor (FII) and Domestic Institutional Investor (DII) data from NSE

Data Source: NSE Archives CSV files
URL Pattern: https://archives.nseindia.com/content/nsccl/fao_participant_vol_DDMMYYYY.csv

Fields captured:
- Date
- FII Buy (contracts)
- FII Sell (contracts)
- FII Net (contracts)
- DII Buy (contracts)
- DII Sell (contracts)
- DII Net (contracts)

Creates new table: fii_dii_data

Usage:
    python scripts/rebuild/14_scrape_fii_dii_data.py --start-date 2020-01-01 --end-date 2025-10-09
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import requests
import csv
from io import StringIO
import argparse

DB_FILE = Path(__file__).parent.parent.parent / "App" / "database" / "stock_market_new.db"
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / f"14_fii_dii_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log_message(message):
    """Log to console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)

    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry + '\n')

def create_fii_dii_table():
    """Create FII/DII data table if not exists"""
    log_message("="*70)
    log_message("CREATE FII/DII DATA TABLE")
    log_message("="*70)
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found")
        return False

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fii_dii_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE NOT NULL,
                fii_buy REAL,
                fii_sell REAL,
                fii_net REAL,
                dii_buy REAL,
                dii_sell REAL,
                dii_net REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_fii_dii_date ON fii_dii_data(date)')

        conn.commit()
        log_message("[OK] FII/DII table created")
        log_message("")

        return True

    except Exception as e:
        log_message(f"[ERROR] Table creation failed: {e}")
        return False

    finally:
        conn.close()

def fetch_fii_dii_for_date(date_str):
    """
    Fetch FII/DII data for a specific date from NSE archives

    Args:
        date_str: Date in YYYY-MM-DD format

    Returns:
        dict with FII/DII data or None if not available
    """
    # Convert to DDMMYYYY format for URL
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    url_date = date_obj.strftime('%d%m%Y')

    url = f"https://archives.nseindia.com/content/nsccl/fao_participant_vol_{url_date}.csv"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 404:
            return None, "NOT_FOUND"

        if response.status_code != 200:
            return None, f"HTTP_{response.status_code}"

        # Parse CSV
        csv_content = response.text
        lines = csv_content.split('\n')

        # Skip header rows (first 2 lines)
        if len(lines) < 3:
            return None, "INVALID_FORMAT"

        data_lines = [l for l in lines[2:] if l.strip()]

        if not data_lines:
            return None, "NO_DATA"

        # Parse CSV data
        reader = csv.reader(data_lines)

        fii_data = None
        dii_data = None

        for row in reader:
            if not row or len(row) < 15:
                continue

            client_type = row[0].strip()

            # FII data (row with "FII")
            if 'FII' in client_type:
                try:
                    total_long = int(row[13].replace(',', '')) if row[13].strip() else 0
                    total_short = int(row[14].replace(',', '')) if row[14].strip() else 0
                    fii_data = {
                        'buy': total_long,
                        'sell': total_short,
                        'net': total_long - total_short
                    }
                except:
                    pass

            # DII data (row with "DII" or "Proprietary")
            elif 'DII' in client_type or 'Prop' in client_type:
                try:
                    total_long = int(row[13].replace(',', '')) if row[13].strip() else 0
                    total_short = int(row[14].replace(',', '')) if row[14].strip() else 0
                    dii_data = {
                        'buy': total_long,
                        'sell': total_short,
                        'net': total_long - total_short
                    }
                except:
                    pass

        if not fii_data and not dii_data:
            return None, "NO_FII_DII_DATA"

        result = {
            'date': date_str,
            'fii_buy': fii_data['buy'] if fii_data else None,
            'fii_sell': fii_data['sell'] if fii_data else None,
            'fii_net': fii_data['net'] if fii_data else None,
            'dii_buy': dii_data['buy'] if dii_data else None,
            'dii_sell': dii_data['sell'] if dii_data else None,
            'dii_net': dii_data['net'] if dii_data else None,
        }

        return result, None

    except Exception as e:
        return None, str(e)

def scrape_fii_dii_data(start_date, end_date):
    """Scrape FII/DII data for date range"""
    log_message("="*70)
    log_message("SCRAPE FII/DII DATA")
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

    try:
        current_dt = start_dt

        while current_dt <= end_dt:
            date_str = current_dt.strftime('%Y-%m-%d')
            day_name = current_dt.strftime('%a')

            # Skip weekends
            if day_name in ['Sat', 'Sun']:
                current_dt += timedelta(days=1)
                continue

            log_message(f"[{current_dt.strftime('%Y-%m-%d')} {day_name}] Fetching...")

            # Check if already exists
            cursor.execute("SELECT date FROM fii_dii_data WHERE date = ?", (date_str,))
            if cursor.fetchone():
                log_message(f"  [SKIP] Already exists")
                skipped_count += 1
                current_dt += timedelta(days=1)
                continue

            # Fetch data
            data, error = fetch_fii_dii_for_date(date_str)

            if error:
                if error == "NOT_FOUND":
                    log_message(f"  [SKIP] No data (holiday/weekend)")
                    not_found_count += 1
                else:
                    log_message(f"  [FAIL] {error}")
                    failed_count += 1

                current_dt += timedelta(days=1)
                continue

            if not data:
                log_message(f"  [FAIL] No data returned")
                failed_count += 1
                current_dt += timedelta(days=1)
                continue

            # Insert into database
            try:
                cursor.execute('''
                    INSERT INTO fii_dii_data (date, fii_buy, fii_sell, fii_net, dii_buy, dii_sell, dii_net)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data['date'],
                    data['fii_buy'],
                    data['fii_sell'],
                    data['fii_net'],
                    data['dii_buy'],
                    data['dii_sell'],
                    data['dii_net']
                ))

                conn.commit()

                log_message(f"  [OK] FII Net: {data['fii_net']:,}, DII Net: {data['dii_net']:,}")
                success_count += 1

            except Exception as e:
                log_message(f"  [FAIL] Database error: {e}")
                failed_count += 1

            current_dt += timedelta(days=1)

        log_message("")
        log_message("="*70)
        log_message("SUMMARY")
        log_message("="*70)
        log_message(f"Total days processed: {total_days}")
        log_message(f"Success: {success_count}")
        log_message(f"Skipped (already exists): {skipped_count}")
        log_message(f"Not found (holiday): {not_found_count}")
        log_message(f"Failed: {failed_count}")
        log_message("")
        log_message(f"[SUCCESS] FII/DII data scraping complete!")
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
    parser = argparse.ArgumentParser(description='Scrape FII/DII data from NSE')
    parser.add_argument('--start-date', type=str, default='2020-01-01', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str, default=datetime.now().strftime('%Y-%m-%d'), help='End date (YYYY-MM-DD)')

    args = parser.parse_args()

    try:
        # Step 1: Create table
        if not create_fii_dii_table():
            exit(1)

        # Step 2: Scrape data
        success = scrape_fii_dii_data(args.start_date, args.end_date)
        exit(0 if success else 1)

    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
