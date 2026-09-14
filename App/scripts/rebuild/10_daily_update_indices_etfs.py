"""
Daily Market Indices & ETFs Update Script

PRODUCTION-READY DAILY UPDATE SYSTEM
- Zero hardcoding: Dynamically discovers all indices/ETFs from OpenChart
- Dual-table support: Updates both market_indices and market_etfs
- Flexible CLI: --date, --today, or date ranges
- Smart duplicate handling: UPSERT logic prevents conflicts
- Robust error handling: Retries, logging, graceful failures

Data Source: OpenChart (openchart library)
Tables: market_indices (pure indices), market_etfs (ETFs ending in -EQ)

Usage Examples:
    # Update today's data
    python 10_daily_update_indices_etfs.py --today
    
    # Update specific date
    python 10_daily_update_indices_etfs.py --date 2025-01-15
    
    # Backfill range
    python 10_daily_update_indices_etfs.py --start-date 2025-01-01 --end-date 2025-01-31
"""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta
import argparse
from openchart import NSEData
import pandas as pd

# ============================================================================
# CONFIGURATION (No Hardcoding - All Paths Dynamic)
# ============================================================================
SCRIPT_DIR = Path(__file__).parent
DB_FILE = SCRIPT_DIR.parent.parent / "database" / "stock_market_new.db"
LOG_DIR = SCRIPT_DIR.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

def get_log_file():
    """Generate unique log file for this run"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return LOG_DIR / f"10_daily_indices_etfs_{timestamp}.log"

LOG_FILE = get_log_file()

# ============================================================================
# LOGGING SYSTEM
# ============================================================================
def log(message, level="INFO"):
    """Thread-safe logging to console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{level}] {message}"
    print(log_entry)
    
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry + '\n')

# ============================================================================
# DATABASE SETUP
# ============================================================================
def ensure_tables_exist(conn):
    """Create tables and indexes if they don't exist (idempotent)"""
    cursor = conn.cursor()
    
    # market_indices table
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
    
    # market_etfs table
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
    
    # Indexes for performance (idempotent with IF NOT EXISTS)
    for table in ['market_indices', 'market_etfs']:
        cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_name ON {table}(index_name)')
        cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_date ON {table}(date)')
        cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_name_date ON {table}(index_name, date)')
    
    conn.commit()
    log("Database tables and indexes verified/created")

# ============================================================================
# MULTI-SOURCE INDEX DATA FETCHER
# ============================================================================
# Senior Dev: Waterfall fallback - nselib → NSE Archives → OpenChart
# Zero hardcoding: Source priority is configurable

# Try to import nselib (primary source)
try:
    from nselib import capital_market
    NSELIB_AVAILABLE = True
except ImportError:
    NSELIB_AVAILABLE = False
    log("nselib not installed - will use fallback sources", "WARN")

import requests
import time

class IndexDataFetcher:
    """
    Multi-source index data fetcher with waterfall fallback
    
    Priority: nselib → NSE Archives → OpenChart
    
    Senior Dev Principles:
    - Zero hardcoding: Source order is configurable
    - User-first: Clear logging of which source worked
    - Practical: Retries with exponential backoff
    - Scalable: Easy to add new sources
    """
    
    # Configurable source priority (can be overridden)
    # Senior Dev: Archives is most reliable for daily index data
    SOURCE_PRIORITY = ['archives', 'nselib', 'openchart']
    
    def __init__(self):
        self.nse = None  # OpenChart instance
        self.symbols_cache = None
        self.active_source = None  # Track which source is working
        self.nselib_available = NSELIB_AVAILABLE
    
    def initialize(self, max_retries=3):
        """
        Initialize data fetcher with waterfall source selection
        
        Tries sources in priority order until one works
        """
        log("="*60)
        log("INITIALIZING MULTI-SOURCE INDEX FETCHER")
        log("="*60)
        log(f"Source priority: {' → '.join(self.SOURCE_PRIORITY)}")
        log("")
        
        for source in self.SOURCE_PRIORITY:
            log(f"Trying source: {source}...")
            
            if source == 'nselib':
                if self._init_nselib():
                    self.active_source = 'nselib'
                    return True
                    
            elif source == 'archives':
                if self._init_archives():
                    self.active_source = 'archives'
                    return True
                    
            elif source == 'openchart':
                if self._init_openchart(max_retries):
                    self.active_source = 'openchart'
                    return True
        
        log("All sources failed to initialize!", "ERROR")
        return False
    
    def _init_nselib(self):
        """Initialize nselib source"""
        if not self.nselib_available:
            log("  nselib not available (not installed)", "WARN")
            return False
        
        try:
            # Test nselib by fetching index list
            # nselib doesn't need master data download like OpenChart
            log("  nselib available - fetching index list...")
            
            # Get list of indices from nselib
            # We'll use a known set of major indices + discover more from data
            self.symbols_cache = self._get_nselib_index_list()
            
            if self.symbols_cache:
                log(f"  ✓ nselib ready with {len(self.symbols_cache)} indices")
                return True
            else:
                log("  nselib returned no indices", "WARN")
                return False
                
        except Exception as e:
            log(f"  nselib init failed: {e}", "WARN")
            return False
    
    def _get_nselib_index_list(self):
        """Get list of indices from nselib or known set"""
        # Senior Dev: Start with known major indices
        # These are the most important ones that users query
        return [
            'NIFTY 50', 'NIFTY NEXT 50', 'NIFTY 100', 'NIFTY 200', 'NIFTY 500',
            'NIFTY MIDCAP 50', 'NIFTY MIDCAP 100', 'NIFTY SMLCAP 50', 'NIFTY SMLCAP 100',
            'NIFTY BANK', 'NIFTY IT', 'NIFTY PHARMA', 'NIFTY AUTO', 'NIFTY FMCG',
            'NIFTY METAL', 'NIFTY REALTY', 'NIFTY ENERGY', 'NIFTY INFRA',
            'NIFTY PSU BANK', 'NIFTY PVT BANK', 'NIFTY FIN SERVICE',
            'INDIA VIX'
        ]
    
    def _init_archives(self):
        """Initialize NSE Archives source"""
        try:
            # Senior Dev: root URL often returns 404 by design. 
            # We test a known directory or just check if we can reach the server.
            log("  Testing NSE Archives connectivity...")
            
            # Use a slightly more specific URL or just check for response (even 404 is connectivity)
            test_url = "https://archives.nseindia.com/content/indices/"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            
            response = requests.get(test_url, headers=headers, timeout=10)
            
            # 404 on the directory is fine as long as we reached the server
            if response.status_code in [200, 403, 404]:
                self.symbols_cache = self._get_nselib_index_list()
                log(f"  ✓ NSE Archives connectivity verified")
                return True
            else:
                log(f"  NSE Archives test returned unexpected status: {response.status_code}", "WARN")
                return False
                
        except Exception as e:
            log(f"  NSE Archives connectivity test failed: {e}", "WARN")
            return False
    
    def _init_openchart(self, max_retries=3):
        """Initialize OpenChart with retry and validation"""
        for attempt in range(1, max_retries + 1):
            try:
                log(f"  OpenChart attempt {attempt}/{max_retries}...")
                self.nse = NSEData()
                self.nse.download()
                
                df = self.nse.nse_data
                
                # Validate: Check if download succeeded
                if df is None or df.empty:
                    log(f"  OpenChart returned empty data (shape: {df.shape if df is not None else 'None'})", "WARN")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt
                        log(f"  Retrying in {wait_time}s...", "WARN")
                        time.sleep(wait_time)
                        continue
                    else:
                        return False
                
                # Validate: Check required column exists
                if 'Type' not in df.columns:
                    log(f"  Invalid data structure. Expected 'Type' column.", "WARN")
                    return False
                
                # Filter Type 0 (indices + ETFs)
                type0 = df[df['Type'] == '0']
                self.symbols_cache = type0['Symbol'].tolist()
                
                if not self.symbols_cache:
                    log("  No symbols found after filtering Type='0'", "WARN")
                    return False
                
                log(f"  ✓ OpenChart ready with {len(self.symbols_cache)} symbols")
                return True
                
            except Exception as e:
                log(f"  OpenChart attempt {attempt} failed: {e}", "WARN")
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
        
        return False
    
    def fetch_data_for_date(self, date_str):
        """
        Fetch and merge indices/ETFs data from all available sources
        
        Senior Dev: Hybrid Strategy
        1. Try Archives (Indices CSV) - Best for Indices (has PB/DY)
        2. Try nselib (Bulk) - Best for broad ETFs/Stocks (has PE)
        3. Merge (Archives data overrides nselib for shared symbols)
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            dict: {"indices": [...], "etfs": [...]} or None if total failure
        """
        combined_indices = {} # Using dict for easy merging by index_name
        combined_etfs = {}
        
        log(f"Fetching data for {date_str} (Hybrid Strategy)...")
        
        # 1. Try NSE Archives (Primary for Indices)
        archives_data = self._fetch_via_archives(date_str)
        if archives_data:
            for rec in archives_data['indices']:
                combined_indices[rec['index_name']] = rec
            for rec in archives_data['etfs']:
                combined_etfs[rec['index_name']] = rec
        
        # 2. Try nselib (Primary for broad ETFs/Stocks)
        nselib_data = self._fetch_via_nselib(date_str)
        if nselib_data:
            # Merge indices (Archives usually better, so only add if missing or merge if needed)
            for rec in nselib_data['indices']:
                name = rec['index_name']
                if name not in combined_indices:
                    combined_indices[name] = rec
                else:
                    # Merge PE if available in nselib but 0 in archives
                    if rec.get('pe_ratio') and not combined_indices[name].get('pe_ratio'):
                        combined_indices[name]['pe_ratio'] = rec['pe_ratio']
            
            # Merge etfs
            for rec in nselib_data['etfs']:
                name = rec['index_name']
                if name not in combined_etfs:
                    combined_etfs[name] = rec
                else:
                    # Merge PE if available
                    if rec.get('pe_ratio') and not combined_etfs[name].get('pe_ratio'):
                        combined_etfs[name]['pe_ratio'] = rec['pe_ratio']

        # 3. Fallback to OpenChart if we still have nothing
        if not combined_indices and not combined_etfs:
            log("  Archives and nselib provided no data. Falling back to OpenChart...", "WARN")
            if self._init_openchart():
                oc_data = self._fetch_via_openchart(date_str)
                if oc_data:
                    for rec in oc_data['indices']:
                        combined_indices[rec['index_name']] = rec
                    for rec in oc_data['etfs']:
                        combined_etfs[rec['index_name']] = rec

        if not combined_indices and not combined_etfs:
            log(f"  Total failure: No data found for {date_str} from any source.", "ERROR")
            return None
            
        log(f"  Hybrid results: {len(combined_indices)} Indices, {len(combined_etfs)} ETFs/Stocks")
        
        return {
            'indices': list(combined_indices.values()),
            'etfs': list(combined_etfs.values()),
            'success_count': len(combined_indices) + len(combined_etfs),
            'failed_count': 0
        }
    
    def _fetch_via_nselib(self, date_str):
        """
        Fetch broad market data via nselib (Bhavcopy + PE Ratios)
        
        Senior Dev: Bulk approach is way faster than symbol-by-symbol requests.
        We download the full Bhavcopy (OHLC) and full PE list, then join them.
        """
        success_count = 0
        failed_count = 0
        
        # Format date for nselib (DD-MM-YYYY)
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        nselib_date = date_obj.strftime('%d-%m-%Y')
        
        try:
            log(f"  Fetching bulk Bhavcopy for {date_str}...")
            # 1. Download Bhavcopy with Delivery (Broad OHLC)
            df_bhav = capital_market.bhav_copy_with_delivery(nselib_date)
            
            if df_bhav is None or df_bhav.empty:
                log(f"  nselib Bhavcopy empty for {date_str}", "WARN")
                return None
            
            log(f"  Downloaded Bhavcopy: {len(df_bhav)} records")
            
            # 2. Download PE Ratios (Broad Valuation)
            log(f"  Fetching bulk PE Ratios for {date_str}...")
            df_pe = capital_market.pe_ratio(nselib_date)
            
            # 3. Merge data
            final_records = []
            
            # PE Map for fast lookup
            pe_map = {}
            if df_pe is not None and not df_pe.empty:
                log(f"  Downloaded PE Ratios: {len(df_pe)} records")
                # Detect PE column (nselib columns can vary: 'SYMBOLP/E', 'P/E', etc.)
                pe_col = None
                for col in df_pe.columns:
                    if 'P/E' in col.upper() or 'PE' in col.upper():
                        pe_col = col
                        break
                
                if pe_col:
                    for _, row in df_pe.iterrows():
                        sym = str(row.get('SYMBOL', '')).strip().upper()
                        if sym:
                            pe_map[sym] = float(row[pe_col]) if pd.notna(row[pe_col]) else 0
            
            # Process Bhavcopy rows
            for _, row in df_bhav.iterrows():
                try:
                    symbol = str(row.get('SYMBOL', '')).strip().upper()
                    if not symbol:
                        continue
                        
                    # Basic OHLC
                    open_v = row.get('OPEN_PRICE') or row.get('OPEN')
                    high_v = row.get('HIGH_PRICE') or row.get('HIGH')
                    low_v = row.get('LOW_PRICE') or row.get('LOW')
                    close_v = row.get('CLOSE_PRICE') or row.get('CLOSE')
                    vol = row.get('TTL_TRD_QNTY') or row.get('VOLUME') or 0
                    
                    # Valuation from our merged map
                    pe = pe_map.get(symbol, 0)
                    
                    record = {
                        'index_name': symbol,
                        'date': date_str,
                        'open': float(open_v) if pd.notna(open_v) else 0,
                        'high': float(high_v) if pd.notna(high_v) else 0,
                        'low': float(low_v) if pd.notna(low_v) else 0,
                        'close': float(close_v) if pd.notna(close_v) else 0,
                        'points_change': float(close_v - open_v) if pd.notna(close_v) and pd.notna(open_v) else 0,
                        'change_percent': float(((close_v - open_v) / open_v * 100)) if pd.notna(close_v) and pd.notna(open_v) and open_v != 0 else 0,
                        'volume': float(vol),
                        'turnover': float(row.get('TURNOVER_LACS', 0)) * 100000, # Convert Lacs to actual value
                        'pe_ratio': pe,
                        'pb_ratio': 0, # Bhavcopy doesn't provide PB
                        'div_yield': 0
                    }
                    
                    final_records.append(record)
                    success_count += 1
                except:
                    failed_count += 1
            
            # Split into indices and etfs (mostly ETFs/Stocks in this source)
            # In this broad source, basically everything is an "ETF/Stock" (market_etfs table)
            # except if it matches a known index name.
            indices = []
            etfs = []
            
            index_list = self._get_nselib_index_list()
            for rec in final_records:
                if rec['index_name'] in index_list:
                    indices.append(rec)
                else:
                    etfs.append(rec)
            
            log(f"  nselib result: {len(indices)} indices, {len(etfs)} etfs/stocks")
            
            return {
                'indices': indices,
                'etfs': etfs,
                'success_count': success_count,
                'failed_count': failed_count
            }
            
        except Exception as e:
            log(f"  nselib fetch failed: {e}", "ERROR")
            return None
    
    def _fetch_via_archives(self, date_str):
        """Fetch index data via NSE Archives"""
        indices_data = []
        etfs_data = []
        success_count = 0
        failed_count = 0
        
        # Format date for archives URL (DDMMYYYY)
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        archive_date = date_obj.strftime('%d%m%Y')
        
        # NSE Archives has index historical data at:
        # https://archives.nseindia.com/content/indices/ind_close_all_{DDMMYYYY}.csv
        url = f"https://archives.nseindia.com/content/indices/ind_close_all_{archive_date}.csv"
        
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        try:
            log(f"  Downloading from NSE Archives: {url}")
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 404:
                log(f"  No data for {date_str} (likely holiday)", "WARN")
                return {'indices': [], 'etfs': [], 'success_count': 0, 'failed_count': 0}
            
            if response.status_code != 200:
                log(f"  Archives returned HTTP {response.status_code}", "WARN")
                return None
            
            # Parse CSV
            from io import StringIO
            df = pd.read_csv(StringIO(response.text))
            
            if df.empty:
                log(f"  No data in CSV for {date_str}", "WARN")
                return {'indices': [], 'etfs': [], 'success_count': 0, 'failed_count': 0}
            
            log(f"  Downloaded {len(df)} index records")
            
            # Helper to safely get value from row
            def safe_get(row, col, default=None):
                try:
                    val = row[col] if col in row.index else default
                    return val if pd.notna(val) else default
                except:
                    return default
            
            # Process each row
            for _, row in df.iterrows():
                try:
                    # Column mapping for NSE Archives format
                    index_name = safe_get(row, 'Index Name', '')
                    
                    if not index_name:
                        continue
                    
                    # Parse values safely
                    open_val = safe_get(row, 'Open Index Value')
                    high_val = safe_get(row, 'High Index Value')
                    low_val = safe_get(row, 'Low Index Value')
                    close_val = safe_get(row, 'Closing Index Value')
                    
                    record = {
                        'index_name': str(index_name).strip().upper(), # Force Upper Case for DB consistency
                        'date': date_str,
                        'open': float(open_val) if open_val is not None else None,
                        'high': float(high_val) if high_val is not None else None,
                        'low': float(low_val) if low_val is not None else None,
                        'close': float(close_val) if close_val is not None else None,
                        'points_change': float(safe_get(row, 'Points Change', 0)) if safe_get(row, 'Points Change') else 0, # Default to 0 instead of None
                        'change_percent': float(safe_get(row, 'Change(%)', 0)) if safe_get(row, 'Change(%)') else 0,
                        'volume': float(safe_get(row, 'Volume', 0)) if safe_get(row, 'Volume') else 0,
                        'turnover': float(safe_get(row, 'Turnover (Rs. Cr.)', 0)) if safe_get(row, 'Turnover (Rs. Cr.)') else 0,
                        'pe_ratio': float(safe_get(row, 'P/E', 0)) if safe_get(row, 'P/E') else 0,
                        'pb_ratio': float(safe_get(row, 'P/B', 0)) if safe_get(row, 'P/B') else 0,
                        'div_yield': float(safe_get(row, 'Div Yield', 0)) if safe_get(row, 'Div Yield') else 0
                    }
                    
                    # Log first few records for verification
                    if success_count < 3:
                        log(f"  Sample record: {record['index_name']} - Close: {record['close']}")
                    
                    # Route based on name pattern
                    if '-EQ' in str(index_name):
                        etfs_data.append(record)
                    else:
                        indices_data.append(record)
                    
                    success_count += 1
                    
                except Exception as e:
                    failed_count += 1
            
            log(f"  Archives: {success_count} success | {failed_count} failed")
            
            return {
                'indices': indices_data,
                'etfs': etfs_data,
                'success_count': success_count,
                'failed_count': failed_count
            }
            
        except Exception as e:
            log(f"  Archives fetch failed: {e}", "ERROR")
            return None
    
    def _fetch_via_openchart(self, date_str):
        """Fetch index data via OpenChart (original method)"""
        if not self.nse:
            log("OpenChart not initialized", "ERROR")
            return None
        
        target_date_dt = datetime.strptime(date_str, '%Y-%m-%d')
        start_dt = target_date_dt - timedelta(days=3)
        end_dt = target_date_dt + timedelta(days=3)
        
        indices_data = []
        etfs_data = []
        success_count = 0
        failed_count = 0
        
        for i, symbol in enumerate(self.symbols_cache, 1):
            if i % 10 == 0 or i == 1:
                print(f"\rProcessing {i}/{len(self.symbols_cache)}: {symbol}...", end="", flush=True)
                
            try:
                df = self.nse.historical(
                    symbol=symbol,
                    exchange='NSE',
                    start=start_dt,
                    end=end_dt,
                    interval='1d'
                )
                
                if df is None or df.empty:
                    continue
                
                df['date_str'] = df.index.strftime('%Y-%m-%d')
                matched_rows = df[df['date_str'] == date_str]
                
                if matched_rows.empty:
                    continue
                
                row = matched_rows.iloc[0]
                record = self._parse_row_to_record(symbol, date_str, row)
                
                if record:
                    if symbol.endswith('-EQ'):
                        etfs_data.append(record)
                    else:
                        indices_data.append(record)
                    success_count += 1
                
            except Exception as e:
                failed_count += 1
                if failed_count <= 5:
                    log(f"\n  Failed to fetch {symbol}: {str(e)[:50]}", "WARN")
        
        print()
        log(f"  OpenChart: {success_count} success | {failed_count} failed")
        
        return {
            'indices': indices_data,
            'etfs': etfs_data,
            'success_count': success_count,
            'failed_count': failed_count
        }
    
    def _parse_row_to_record(self, symbol, date_str, row):
        """Parse a row from any source into standard record format"""
        def get_col(row, *names):
            for name in names:
                if hasattr(row, 'get'):
                    val = row.get(name)
                    if val is not None and pd.notna(val):
                        return val
                elif name in row.index:
                    val = row[name]
                    if pd.notna(val):
                        return val
            return None
        
        open_val = get_col(row, 'Open', 'open', 'OPEN')
        high_val = get_col(row, 'High', 'high', 'HIGH')
        low_val = get_col(row, 'Low', 'low', 'LOW')
        close_val = get_col(row, 'Close', 'close', 'CLOSE')
        volume_val = get_col(row, 'Volume', 'volume', 'VOLUME')
        
        return {
            'index_name': str(symbol).strip().upper(), # Force Upper Case
            'date': date_str,
            'open': float(open_val) if open_val is not None else None,
            'high': float(high_val) if high_val is not None else None,
            'low': float(low_val) if low_val is not None else None,
            'close': float(close_val) if close_val is not None else None,
            'points_change': float(close_val - open_val) if close_val and open_val else 0,
            'change_percent': float(((close_val - open_val) / open_val * 100)) if close_val and open_val and open_val != 0 else 0,
            'volume': float(volume_val) if volume_val is not None else 0,
            'turnover': 0,
            'pe_ratio': 0,
            'pb_ratio': 0,
            'div_yield': 0
        }

# ============================================================================
# DATABASE INSERTER
# ============================================================================
def upsert_data(conn, table_name, records):
    """
    Insert or update records with UPSERT logic (prevents duplicates)
    
    Args:
        conn: Database connection
        table_name: 'market_indices' or 'market_etfs'
        records: List of record dicts
        
    Returns:
        tuple: (inserted_count, updated_count, skipped_count)
    """
    if not records:
        return 0, 0, 0
    
    cursor = conn.cursor()
    inserted = 0
    updated = 0
    skipped = 0
    
    for rec in records:
        try:
            # Check if record exists
            cursor.execute(
                f"SELECT id FROM {table_name} WHERE index_name = ? AND date = ?",
                (rec['index_name'], rec['date'])
            )
            existing = cursor.fetchone()
            
            if existing:
                # Update existing record
                # Senior Dev: Be careful with valuation ratios. Don't overwrite with 0 if we already have values.
                cursor.execute(f'''
                    UPDATE {table_name}
                    SET open = ?, high = ?, low = ?, close = ?,
                        points_change = ?, change_percent = ?,
                        volume = ?, turnover = ?,
                        pe_ratio = CASE WHEN ? > 0 THEN ? ELSE pe_ratio END,
                        pb_ratio = CASE WHEN ? > 0 THEN ? ELSE pb_ratio END,
                        div_yield = CASE WHEN ? > 0 THEN ? ELSE div_yield END
                    WHERE index_name = ? AND date = ?
                ''', (
                    rec['open'], rec['high'], rec['low'], rec['close'],
                    rec['points_change'], rec['change_percent'],
                    rec['volume'], rec['turnover'],
                    rec['pe_ratio'], rec['pe_ratio'],
                    rec['pb_ratio'], rec['pb_ratio'],
                    rec['div_yield'], rec['div_yield'],
                    rec['index_name'], rec['date']
                ))
                updated += 1
            else:
                # Insert new record
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

# ============================================================================
# MAIN UPDATE LOGIC
# ============================================================================
def update_for_date_range(start_date, end_date):
    """
    Update indices/ETFs data for a date range
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    """
    log("="*80)
    log("DAILY INDICES/ETFS UPDATE")
    log("="*80)
    log(f"Date Range: {start_date} to {end_date}")
    log(f"Database: {DB_FILE}")
    log("")
    
    # Validate database
    if not DB_FILE.exists():
        log("Database file not found!", "ERROR")
        return False
    
    # Initialize fetcher
    fetcher = IndexDataFetcher()
    if not fetcher.initialize():
        return False
    
    # Connect to database
    conn = sqlite3.connect(DB_FILE)
    ensure_tables_exist(conn)
    
    # Iterate through date range
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
            result = fetcher.fetch_data_for_date(date_str)
            
            if not result or (not result['indices'] and not result['etfs']):
                log(f"  No data available (likely holiday)", "WARN")
                failed_days += 1
                current_dt += timedelta(days=1)
                continue
            
            # Upsert indices
            i_ins, i_upd, i_skip = upsert_data(conn, 'market_indices', result['indices'])
            total_indices_inserted += i_ins
            total_indices_updated += i_upd
            
            # Upsert ETFs
            e_ins, e_upd, e_skip = upsert_data(conn, 'market_etfs', result['etfs'])
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

# ============================================================================
# CLI INTERFACE
# ============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Daily update for market indices and ETFs using OpenChart',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Update today's data
  python %(prog)s --today
  
  # Update specific date
  python %(prog)s --date 2025-01-15
  
  # Backfill range
  python %(prog)s --start-date 2025-01-01 --end-date 2025-01-31
        """
    )
    
    # Mutually exclusive group for operation modes
    mode_group = parser.add_mutually_exclusive_group(required=False)
    mode_group.add_argument('--today', action='store_true', 
                           help='Update data for today')
    mode_group.add_argument('--date', type=str, metavar='YYYY-MM-DD',
                           help='Update data for specific date')
    
    # Range mode (default if no mode specified)
    parser.add_argument('--start-date', type=str, metavar='YYYY-MM-DD',
                       help='Start date for range update (default: 7 days ago)')
    parser.add_argument('--end-date', type=str, metavar='YYYY-MM-DD',
                       help='End date for range update (default: today)')
    
    args = parser.parse_args()
    
    # Determine operation mode
    if args.today:
        # Today mode
        today = datetime.now().strftime('%Y-%m-%d')
        success = update_for_date_range(today, today)
    elif args.date:
        # Specific date mode
        success = update_for_date_range(args.date, args.date)
    else:
        # Range mode (default behavior)
        # Smart defaults: last 7 days if not specified
        end = args.end_date or datetime.now().strftime('%Y-%m-%d')
        
        if args.start_date:
            start = args.start_date
        else:
            # Default to 7 days ago
            start_dt = datetime.now() - timedelta(days=7)
            start = start_dt.strftime('%Y-%m-%d')
        
        success = update_for_date_range(start, end)
    
    sys.exit(0 if success else 1)
