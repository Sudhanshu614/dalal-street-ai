"""
DAILY MARKET INDICES & ETFs UPDATE - LIGHTWEIGHT VERSION

Uses NSE Archives CSV (one download for ALL indices) instead of OpenChart API
Much faster and more reliable for daily updates.

Features:
- Single CSV download covers all indices/ETFs for a date
- Automatic routing to market_indices or market_etfs tables
- UPSERT logic prevents duplicates
- Smart weekend/holiday detection

Usage:
    python 10_daily_update_nse.py --today
    python 10_daily_update_nse.py --date 2025-11-20
    python 10_daily_update_nse.py --start-date 2025-11-01 --end-date 2025-11-20
"""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta
import argparse
import requests
import csv
from io import StringIO

# Configuration
SCRIPT_DIR = Path(__file__).parent
DB_FILE = SCRIPT_DIR.parent.parent / "database" / "stock_market_new.db"
LOG_DIR = SCRIPT_DIR.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

def get_log_file():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return LOG_DIR / f"10_daily_update_nse_{timestamp}.log"

LOG_FILE = get_log_file()

def log(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{level}] {message}"
    print(log_entry)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry + '\n')

def ensure_tables_exist(conn):
    """Create tables and indexes if they don't exist"""
    cursor = conn.cursor()
    
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
            pe_ratio REAL DEFAULT 0,
            pb_ratio REAL DEFAULT 0,
            div_yield REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(index_name, date)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS market_etfs (
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
            pe_ratio REAL DEFAULT 0,
            pb_ratio REAL DEFAULT 0,
            div_yield REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(index_name, date)
        )
    ''')
    
    for table in ['market_indices', 'market_etfs']:
        cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_name ON {table}(index_name)')
        cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_date ON {table}(date)')
        cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_name_date ON {table}(index_name, date)')
    
    conn.commit()

def parse_number(text):
    """Parse number from text"""
    if not text or text.strip() == '' or text.strip() == '-':
        return None
    try:
        return float(text.replace(',', '').strip())
    except:
        return None

def fetch_nse_data_for_date(date_str):
    """
    Fetch ALL indices data for a date from NSE Archives
    Returns list of dicts with index data
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
            return None, "NOT_FOUND"
        
        if response.status_code != 200:
            return None, f"HTTP_{response.status_code}"
        
        # Parse CSV
        csv_content = response.text
        reader = csv.DictReader(StringIO(csv_content))
        
        indices_data = []
        
        for row in reader:
            index_name = row.get('Index Name', '').strip()
            
            if not index_name:
                continue
            
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
                'pe_ratio': parse_number(row.get('P/E', '') or '0') or 0,
                'pb_ratio': parse_number(row.get('P/B', '') or '0') or 0,
                'div_yield': parse_number(row.get('Div Yield', '') or '0') or 0
            }
            
            indices_data.append(index_data)
        
        return indices_data, None
        
    except Exception as e:
        return None, str(e)

def upsert_data(conn, table_name, records):
    """
    Insert or update records with UPSERT logic
    Returns (inserted, updated, skipped)
    """
    if not records:
        return 0, 0, 0
    
    cursor = conn.cursor()
    inserted = 0
    updated = 0
    skipped = 0
    
    for rec in records:
        try:
            # Check if exists
            cursor.execute(
                f"SELECT id FROM {table_name} WHERE index_name = ? AND date = ?",
                (rec['index_name'], rec['date'])
            )
            existing = cursor.fetchone()
            
            if existing:
                # Update existing
                cursor.execute(f'''
                    UPDATE {table_name}
                    SET open = ?, high = ?, low = ?, close = ?,
                        points_change = ?, change_percent = ?,
                        volume = ?, turnover = ?, pe_ratio = ?, pb_ratio = ?, div_yield = ?
                    WHERE index_name = ? AND date = ?
                ''', (
                    rec['open'], rec['high'], rec['low'], rec['close'],
                    rec['points_change'], rec['change_percent'],
                    rec['volume'], rec['turnover'],
                    rec['pe_ratio'], rec['pb_ratio'], rec['div_yield'],
                    rec['index_name'], rec['date']
                ))
                updated += 1
            else:
                # Insert new
                cursor.execute(f'''
                    INSERT INTO {table_name}
                    (index_name, date, open, high, low, close, points_change, change_percent,
                     volume, turnover, pe_ratio, pb_ratio, div_yield)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    rec['index_name'], rec['date'],
                    rec['open'], rec['high'], rec['low'], rec['close'],
                    rec['points_change'], rec['change_percent'],
                    rec['volume'], rec['turnover'],
                    rec['pe_ratio'], rec['pb_ratio'], rec['div_yield']
                ))
                inserted += 1
                
        except Exception as e:
            log(f"  Insert error for {rec['index_name']}: {e}", "ERROR")
            skipped += 1
            continue
    
    conn.commit()
    return inserted, updated, skipped

def update_for_date_range(start_date, end_date):
    """Update indices/ETFs data for a date range"""
    log("="*80)
    log("DAILY INDICES/ETFS UPDATE (NSE Archives)")
    log("="*80)
    log(f"Date Range: {start_date} to {end_date}")
    log(f"Database: {DB_FILE}")
    log("")
    
    if not DB_FILE.exists():
        log("Database file not found!", "ERROR")
        return False
    
    conn = sqlite3.connect(DB_FILE)
    ensure_tables_exist(conn)
    
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    current_dt = start_dt
    
    total_days = 0
    success_days = 0
    failed_days = 0
    total_indices_inserted = 0
    total_indices_updated = 0
    total_etfs_inserted = 0
    total_etfs_updated = 0
    
    try:
        while current_dt <= end_dt:
            date_str = current_dt.strftime('%Y-%m-%d')
            day_name = current_dt.strftime('%a')
            
            # Skip weekends
            if day_name in ['Sat', 'Sun']:
                log(f"[{date_str} {day_name}] Skipping weekend")
                current_dt += timedelta(days=1)
                continue
            
            total_days += 1
            log(f"[{date_str} {day_name}] Processing...")
            
            # Fetch data
            data, error = fetch_nse_data_for_date(date_str)
            
            if error:
                if error == "NOT_FOUND":
                    log(f"  No data available (likely holiday)", "WARN")
                else:
                    log(f"  Failed: {error}", "ERROR")
                failed_days += 1
                current_dt += timedelta(days=1)
                continue
            
            if not data:
                log(f"  No data available", "WARN")
                failed_days += 1
                current_dt += timedelta(days=1)
                continue
            
            # Route to indices or ETFs based on -EQ suffix
            indices_records = [rec for rec in data if not rec['index_name'].endswith('-EQ')]
            etf_records = [rec for rec in data if rec['index_name'].endswith('-EQ')]
            
            # Upsert to appropriate tables
            i_ins, i_upd, i_skip = upsert_data(conn, 'market_indices', indices_records)
            total_indices_inserted += i_ins
            total_indices_updated += i_upd
            
            e_ins, e_upd, e_skip = upsert_data(conn, 'market_etfs', etf_records)
            total_etfs_inserted += e_ins
            total_etfs_updated += e_upd
            
            log(f"  Indices: {i_ins} inserted, {i_upd} updated, {i_skip} skipped")
            log(f"  ETFs: {e_ins} inserted, {e_upd} updated, {e_skip} skipped")
            
            success_days += 1
            current_dt += timedelta(days=1)
        
        # Summary
        log("")
        log("="*80)
        log("UPDATE SUMMARY")
        log("="*80)
        log(f"Total Days Processed: {total_days}")
        log(f"Success: {success_days} | Failed: {failed_days}")
        log(f"Indices - Inserted: {total_indices_inserted} | Updated: {total_indices_updated}")
        log(f"ETFs - Inserted: {total_etfs_inserted} | Updated: {total_etfs_updated}")
        log(f"Log File: {LOG_FILE}")
        log("")
        
        return True
        
    except Exception as e:
        log(f"Update failed: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Daily update for market indices and ETFs using NSE Archives',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    mode_group = parser.add_mutually_exclusive_group(required=False)
    mode_group.add_argument('--today', action='store_true',
                           help='Update data for today')
    mode_group.add_argument('--date', type=str, metavar='YYYY-MM-DD',
                           help='Update data for specific date')
    
    parser.add_argument('--start-date', type=str, metavar='YYYY-MM-DD',
                       help='Start date for range update (default: 7 days ago)')
    parser.add_argument('--end-date', type=str, metavar='YYYY-MM-DD',
                       help='End date for range update (default: today)')
    
    args = parser.parse_args()
    
    # Determine operation mode
    if args.today:
        today = datetime.now().strftime('%Y-%m-%d')
        success = update_for_date_range(today, today)
    elif args.date:
        success = update_for_date_range(args.date, args.date)
    else:
        # Range mode with smart defaults
        end = args.end_date or datetime.now().strftime('%Y-%m-%d')
        if args.start_date:
            start = args.start_date
        else:
            start_dt = datetime.now() - timedelta(days=7)
            start = start_dt.strftime('%Y-%m-%d')
        success = update_for_date_range(start, end)
    
    sys.exit(0 if success else 1)
