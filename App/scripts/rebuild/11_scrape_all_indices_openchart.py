"""
PHASE 11: SCRAPE ALL MARKET INDICES (OPENCHART) - DYNAMIC VERSION
Downloads FULL history for ALL available indices using OpenChart library.

Features:
- 100% Dynamic Discovery (No hardcoded lists)
- Uses OpenChart master data (Type='0') to find all indices
- Downloads data from 1990-01-01 to Present
- Replaces existing market_indices table
- Robust error handling and retries
"""

import sqlite3
import pandas as pd
from datetime import datetime, date, timedelta
import time
from pathlib import Path
import sys

try:
    from openchart import NSEData
except ImportError:
    print("[ERROR] openchart library not installed!")
    sys.exit(1)

# Configuration
DB_FILE = Path(__file__).parent.parent.parent / "database" / "stock_market_new.db"
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"11_scrape_all_indices_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

START_DATE = datetime(1990, 1, 1)
END_DATE = datetime.now()

def log_message(message, console=True, file=True):
    """Log to console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"

    if console:
        try:
            print(log_entry)
        except:
            pass

    if file:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_entry + '\n')

def get_db_connection():
    return sqlite3.connect(DB_FILE, timeout=30.0)

def setup_database():
    """Create/Reset market_indices table"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Drop existing table to ensure clean slate
    log_message("Dropping existing market_indices table...")
    cursor.execute("DROP TABLE IF EXISTS market_indices")
    
    # Recreate table
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
    
    # Create indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_indices_name ON market_indices(index_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_indices_date ON market_indices(date)')
    
    conn.commit()
    conn.close()
    log_message("Database table recreated successfully.")

def discover_indices_dynamic(nse):
    """Discover ALL indices dynamically from master data"""
    log_message("Discovering indices from master data...")
    
    if not hasattr(nse, 'nse_data'):
        log_message("[ERROR] nse.nse_data not found. Did download() fail?")
        return []
        
    df = nse.nse_data
    
    # Filter for Type == '0' (Indices)
    # Note: 'Type' column might be string '0' or int 0
    # Also filter out the header row if present (where ScripCode == 'ScripCode')
    
    try:
        # Convert Type to string for consistency
        df['Type'] = df['Type'].astype(str)
        
        # Filter: Type is '0' AND ScripCode is not 'ScripCode'
        indices_df = df[
            (df['Type'] == '0') & 
            (df['ScripCode'] != 'ScripCode')
        ]
        
        indices = indices_df['Symbol'].unique().tolist()
        indices = sorted([str(i) for i in indices if i])
        
        log_message(f"Found {len(indices)} indices in master data.")
        
        # Log first 10 and last 10 for verification
        if indices:
            log_message(f"First 5: {indices[:5]}")
            log_message(f"Last 5: {indices[-5:]}")
            
        return indices
        
    except Exception as e:
        log_message(f"[ERROR] Failed to filter master data: {e}")
        return []

def download_index_history(nse, symbol, conn):
    """Download full history for an index"""
    try:
        # OpenChart expects datetime objects
        data = nse.historical(
            symbol=symbol,
            exchange='NSE',
            start=START_DATE,
            end=END_DATE,
            interval='1d'
        )
        
        if data is None or data.empty:
            return False, 0, "No data"
            
        # Prepare records for insertion
        records = []
        for timestamp, row in data.iterrows():
            date_str = timestamp.strftime('%Y-%m-%d')
            
            # Handle potential missing columns
            open_val = row.get('Open', 0)
            high_val = row.get('High', 0)
            low_val = row.get('Low', 0)
            close_val = row.get('Close', 0)
            volume_val = row.get('Volume', 0)
            
            records.append((
                symbol,         # index_name
                date_str,       # date
                open_val,
                high_val,
                low_val,
                close_val,
                0,              # points_change (calc later)
                0,              # change_percent
                volume_val,
                0,              # turnover
                0,              # pe
                0,              # pb
                0               # div_yield
            ))
            
        # Bulk insert
        cursor = conn.cursor()
        cursor.executemany('''
            INSERT OR IGNORE INTO market_indices 
            (index_name, date, open, high, low, close, points_change, change_percent, volume, turnover, pe_ratio, pb_ratio, div_yield)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', records)
        
        conn.commit()
        
        start_dt = data.index.min().strftime('%Y-%m-%d')
        end_dt = data.index.max().strftime('%Y-%m-%d')
        
        return True, len(records), f"{start_dt} to {end_dt}"
        
    except Exception as e:
        return False, 0, str(e)

def main():
    log_message("="*80)
    log_message("PHASE 11: SCRAPE ALL INDICES (OPENCHART) - DYNAMIC")
    log_message("="*80)
    
    # 1. Initialize OpenChart
    log_message("Initializing OpenChart...")
    try:
        nse = NSEData()
        log_message("Downloading Master Data...")
        nse.download()
    except Exception as e:
        log_message(f"[FATAL] OpenChart init failed: {e}")
        return

    # 2. Setup Database
    setup_database()
    conn = get_db_connection()
    
    # 3. Discover Indices (Dynamic)
    indices = discover_indices_dynamic(nse)
    
    if not indices:
        log_message("[ERROR] No indices found! Aborting.")
        return
    
    # 4. Download Loop
    success_count = 0
    fail_count = 0
    total_records = 0
    
    log_message(f"Starting download for {len(indices)} indices...")
    log_message("-" * 80)
    
    for i, symbol in enumerate(indices, 1):
        log_message(f"[{i}/{len(indices)}] Downloading {symbol}...", console=True, file=False)
        
        success, count, info = download_index_history(nse, symbol, conn)
        
        if success:
            log_message(f"  ✅ {symbol}: {count} records ({info})")
            success_count += 1
            total_records += count
        else:
            log_message(f"  ❌ {symbol}: Failed ({info})")
            fail_count += 1
            
        # Small delay to be polite
        # time.sleep(0.2) 
        
    conn.close()
    
    log_message("="*80)
    log_message("DOWNLOAD COMPLETE")
    log_message("="*80)
    log_message(f"Total Indices: {len(indices)}")
    log_message(f"Successful: {success_count}")
    log_message(f"Failed: {fail_count}")
    log_message(f"Total Records Inserted: {total_records:,}")
    log_message(f"Log file: {LOG_FILE}")

if __name__ == "__main__":
    main()
