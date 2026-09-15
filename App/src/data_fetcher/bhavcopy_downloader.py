"""
Daily Bhavcopy Downloader - Production Ready

Downloads and processes daily bhavcopy (equity market data) from NSE
Tracks active tickers, detects disappeared tickers (ticker changes)

Data Sources:
- Primary: NSE Archives (official CSV)
- Backup: jugaad-data (NSE equity bhavcopy)
- Fallback: nselib (market data)

Reference: FROM_SCRATCH_DOCS/TICKER_RESOLUTION_STRATEGY.md:273-330
"""

import sqlite3
import pandas as pd
import requests
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
import time
import json

# API imports (with fallback)
try:
    from jugaad_data.nse import full_bhavcopy_raw  # Use full_bhavcopy_raw (works!) not bhavcopy_eq
    JUGAAD_AVAILABLE = True
except ImportError:
    JUGAAD_AVAILABLE = False
    print("[WARN] jugaad-data not available for bhavcopy download")

try:
    from nselib import capital_market
    NSELIB_AVAILABLE = True
except ImportError:
    NSELIB_AVAILABLE = False
    print("[WARN] nselib not available for bhavcopy download")


class BhavcopyColumnMapper:
    """
    Maps various bhavcopy column name formats to standardized names
    Handles NSE, jugaad-data, and nselib format variations
    """
    
    COLUMN_ALIASES = {
        'symbol': ['SYMBOL', 'Symbol', 'STOCK', 'SCRIP'],
        'date': ['DATE1', 'DATE', 'TradeDate', 'TRADE_DATE', 'TIMESTAMP'],
        'open': ['OPEN_PRICE', 'OPEN', 'OpenPrice', 'Open'],
        'high': ['HIGH_PRICE', 'HIGH', 'HighPrice', 'High'],
        'low': ['LOW_PRICE', 'LOW', 'LowPrice', 'Low'],
        'close': ['CLOSE_PRICE', 'CLOSE', 'ClosePrice', 'Close', 'LAST_PRICE'],
        'volume': ['TTL_TRD_QNTY', 'TOTTRDQTY', 'Volume', 'VOLUME', 'TotalTradedQuantity'],
        'prev_close': ['PREV_CLOSE', 'PREVCLOSE', 'PrevClose', 'Previous Close'],
        'series': ['SERIES', 'Series', 'SEGMENT']
    }
    
    @classmethod
    def detect_columns(cls, df: pd.DataFrame) -> dict:
        """
        Auto-detect column mappings from DataFrame
        
        Returns:
            dict: {standardized_name: actual_column_name}
        """
        mapping = {}
        available_cols = set(df.columns)
        
        for standard_name, aliases in cls.COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in available_cols:
                    mapping[standard_name] = alias
                    break
        
        return mapping
    
    @classmethod
    def validate_required_columns(cls, mapping: dict) -> Tuple[bool, str]:
        """
        Check if all required OHLC columns are present
        
        Returns:
            (is_valid, error_message)
        """
        required = ['symbol', 'open', 'high', 'low', 'close']
        missing = [col for col in required if col not in mapping]
        
        if missing:
            return False, f"Missing required columns: {', '.join(missing)}"
        
        return True, ""


class OHLCValidator:
    """
    Validates OHLC data integrity before database insertion
    """
    
    @staticmethod
    def validate_row(row: dict) -> Tuple[bool, str]:
        """
        Validate a single OHLC row
        
        Returns:
            (is_valid, error_message)
        """
        symbol = row.get('symbol')
        
        # Check required fields
        if not symbol:
            return False, "Missing symbol"
        
        open_price = row.get('open')
        high = row.get('high')
        low = row.get('low')
        close = row.get('close')
        
        # Check for nulls
        if any(x is None for x in [open_price, high, low, close]):
            return False, f"{symbol}: Missing OHLC values"
        
        # Convert to float and validate
        try:
            o, h, l, c = float(open_price), float(high), float(low), float(close)
        except (ValueError, TypeError):
            return False, f"{symbol}: Invalid number format"
        
        # Price sanity checks
        if any(x <= 0 for x in [o, h, l, c]):
            return False, f"{symbol}: Non-positive prices not allowed"
        
        if h < l:
            return False, f"{symbol}: High ({h}) < Low ({l})"
        
        if not (l <= o <= h and l <= c <= h):
            # Allow small floating point errors
            if not (l <= o + 0.05 and o <= h + 0.05 and l <= c + 0.05 and c <= h + 0.05):
                return False, f"{symbol}: Open/Close outside High/Low range"
        
        # Volume check (allow 0 for illiquid stocks)
        volume = row.get('volume', 0)
        try:
            volume = int(volume)
            if volume < 0:
                return False, f"{symbol}: Negative volume"
        except (ValueError, TypeError):
            return False, f"{symbol}: Invalid volume format"
        
        return True, ""


class BhavcopyDownloader:
    """
    Download and process daily NSE bhavcopy (equity market data)

    Functionality:
    - Download daily bhavcopy from NSE
    - Extract active ticker list
    - Compare with previous day (detect disappeared tickers)
    - Store in database for historical tracking
    - Trigger ticker resolution for disappeared tickers
    """

    def __init__(self, db_path: str, cache_dir: str = "cache/bhavcopy", enable_ipo_detection: bool = False, enable_demerger_correlation: bool = True, enable_ticker_tracking: bool = True):
        """
        Initialize bhavcopy downloader

        Args:
            db_path: Path to SQLite database
            cache_dir: Directory to cache bhavcopy files
            enable_ipo_detection: Enable IPO detection
            enable_demerger_correlation: Enable demerger correlation
            enable_ticker_tracking: Enable tracking of new/disappeared tickers and updating stocks_master
        """
        self.db_path = Path(db_path)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.enable_ipo_detection = enable_ipo_detection
        self.enable_demerger_correlation = enable_demerger_correlation
        self.enable_ticker_tracking = enable_ticker_tracking

        # Initialize database connection
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass

        # Create bhavcopy tracking table if doesn't exist
        self._create_tracking_table()

    def _create_tracking_table(self):
        """Create table to track daily bhavcopy downloads"""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bhavcopy_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                total_tickers INTEGER,
                new_tickers TEXT,
                disappeared_tickers TEXT,
                download_timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                data_source TEXT
            )
        """)
        self.conn.commit()

    def download_bhavcopy(self, date: Optional[datetime] = None) -> Tuple[pd.DataFrame, str]:
        """
        Download bhavcopy for a specific date using multi-tier fallback

        Args:
            date: Date to download (defaults to today, or latest trading day)

        Returns:
            Tuple of (DataFrame with bhavcopy data, data_source used)
        """
        if date is None:
            date = datetime.now()

        # Try Tier 1: jugaad-data full_bhavcopy_raw (fastest, most reliable)
        if JUGAAD_AVAILABLE:
            try:
                print(f"[INFO] Attempting bhavcopy download via jugaad-data for {date.strftime('%Y-%m-%d')}")

                # full_bhavcopy_raw returns CSV string, not DataFrame
                csv_data = full_bhavcopy_raw(date)

                if csv_data and len(csv_data) > 0:
                    txt = str(csv_data).strip()
                    if ('<html' in txt.lower()) or ('<!doctype' in txt.lower()) or ('The file you are trying to access' in txt):
                        raise Exception('non_csv_payload')
                    # Parse CSV string into DataFrame
                    import io
                    df = pd.read_csv(io.StringIO(csv_data), engine='python')

                    # Clean column names (some have leading spaces)
                    df.columns = df.columns.str.strip()

                    # Clean string values (strip whitespace from all object columns)
                    for col in df.select_dtypes(include=['object']).columns:
                        df[col] = df[col].str.strip()

                    print(f"[OK] Downloaded {len(df)} records via jugaad-data")
                    return df, 'jugaad_data'
            except Exception as e:
                print(f"[WARN] jugaad-data failed: {e}")

        # Try Tier 2: NSE Archives direct download
        try:
            print(f"[INFO] Attempting bhavcopy download from NSE Archives")
            df = self._download_from_nse_archives(date)

            if df is not None and not df.empty:
                print(f"[OK] Downloaded {len(df)} records from NSE Archives")
                return df, 'nse_archives'
        except Exception as e:
            print(f"[WARN] NSE Archives failed: {e}")

        # Try Tier 3: nselib
        if NSELIB_AVAILABLE:
            try:
                print(f"[INFO] Attempting bhavcopy download via nselib")
                df = self._download_via_nselib(date)

                if df is not None and not df.empty:
                    print(f"[OK] Downloaded {len(df)} records via nselib")
                    return df, 'nselib'
            except Exception as e:
                print(f"[WARN] nselib failed: {e}")

        # All sources failed
        raise Exception("All bhavcopy download sources failed")

    def _download_from_nse_archives(self, date: datetime) -> pd.DataFrame:
        """
        Download bhavcopy directly from NSE Archives

        URL format: https://nsearchives.nseindia.com/content/historical/EQUITIES/YYYY/MMM/cm{DD}{MMM}{YYYY}bhav.csv.zip
        """
        # Format date for NSE URL
        day = date.strftime('%d')
        month = date.strftime('%b').upper()
        year = date.strftime('%Y')

        url = f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{year}/{month}/cm{day}{month}{year}bhav.csv.zip"

        # Download with headers (NSE requires browser-like headers)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.nseindia.com/'
        }

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        # Read CSV from zip
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            csv_filename = z.namelist()[0]
            with z.open(csv_filename) as f:
                df = pd.read_csv(f)

        return df

    def _download_via_nselib(self, date: datetime) -> pd.DataFrame:
        """
        Download bhavcopy using nselib (constructs from market data)

        Note: This is a fallback - may not have complete data
        """
        # Get all equity symbols
        try:
            if not NSELIB_AVAILABLE:
                return pd.DataFrame()
            df = None
            try:
                df = capital_market.bhav_copy_with_delivery()
            except Exception:
                try:
                    df = capital_market.bhav_copy_equities()
                except Exception:
                    df = None
            if df is None:
                return pd.DataFrame()
            if hasattr(df, 'columns'):
                df.columns = df.columns.str.strip()
            return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
        except Exception as e:
            raise Exception(f"nselib bhavcopy construction failed: {e}")

    def process_bhavcopy(self, df: pd.DataFrame, data_source: str, date: datetime) -> Dict:
        """
        Process bhavcopy data and update database

        Returns:
            Dictionary with processing results (new/disappeared tickers)
        """
        # Extract active tickers (SERIES='EQ' for equity)
        if 'SERIES' in df.columns:
            equity_df = df[df['SERIES'] == 'EQ']
        else:
            equity_df = df  # Assume all are equity if no SERIES column

        current_tickers = set(equity_df['SYMBOL'].unique())

        # Load OHLC data into database (always done)
        ohlc_stats = self.load_bhavcopy_to_ohlc(df, date)

        # Skip tracking if disabled (pure OHLC scrape mode)
        if not self.enable_ticker_tracking:
            return {
                'total_tickers': len(current_tickers),
                'new_tickers': set(),
                'disappeared_tickers': set(),
                'ticker_changes': [],
                'detected_ipos': [],
                'ohlc_inserted': ohlc_stats['inserted'],
                'ohlc_skipped': ohlc_stats['skipped'],
                'ohlc_failed': ohlc_stats['failed'],
                'data_source': data_source
            }

        # Reactivate any symbols present today
        try:
            cursor = self.conn.cursor()
            if current_tickers:
                # Senior Dev: Sqlite has a parameter limit (usually 999). Must batch.
                current_list = list(current_tickers)
                batch_size = 900
                to_reactivate = []
                
                for i in range(0, len(current_list), batch_size):
                    batch = current_list[i:i + batch_size]
                    placeholders = ','.join('?' * len(batch))
                    cursor.execute(f"SELECT symbol FROM stocks_master WHERE is_active = 0 AND symbol IN ({placeholders})", batch)
                    to_reactivate.extend([row['symbol'] for row in cursor.fetchall()])
                
                if to_reactivate:
                    for i in range(0, len(to_reactivate), batch_size):
                        batch = to_reactivate[i:i + batch_size]
                        ph2 = ','.join('?' * len(batch))
                        cursor.execute(f"UPDATE stocks_master SET is_active = 1, updated_at = CURRENT_TIMESTAMP WHERE symbol IN ({ph2})", batch)
                self.conn.commit()
        except Exception as e:
            print(f"[WARN] Reactivation failed: {e}")
            pass

        # Load yesterday's tickers
        yesterday = date - timedelta(days=1)
        yesterday_tickers = self._load_tickers_for_date(yesterday)

        # Calculate differences
        new_tickers = current_tickers - yesterday_tickers if yesterday_tickers else set()
        disappeared_tickers = yesterday_tickers - current_tickers if yesterday_tickers else set()

        # Auto-correlate ticker changes (link disappeared -> new tickers)
        ticker_changes = []
        if self.enable_demerger_correlation:
            ticker_changes = self._correlate_ticker_changes(
                disappeared_tickers, new_tickers, date, equity_df
            )

        # Detect IPO listings (new tickers that are truly new, not renamed)
        detected_ipos = []
        if self.enable_ipo_detection:
            detected_ipos = self._detect_ipo_listings(
                new_tickers, ticker_changes, date, equity_df
            )

        # Store bhavcopy history
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO bhavcopy_history
            (date, total_tickers, new_tickers, disappeared_tickers, data_source)
            VALUES (?, ?, ?, ?, ?)
        """, (
            date.strftime('%Y-%m-%d'),
            len(current_tickers),
            json.dumps(list(new_tickers)),
            json.dumps(list(disappeared_tickers)),
            data_source
        ))
        self.conn.commit()

        # Update stocks_master table (mark inactive tickers)
        if disappeared_tickers:
            try:
                dis_list = list(disappeared_tickers)
                batch_size = 900
                for i in range(0, len(dis_list), batch_size):
                    batch = dis_list[i:i + batch_size]
                    placeholders = ','.join('?' * len(batch))
                    cursor.execute(f"""
                        UPDATE stocks_master
                        SET is_active = 0, updated_at = CURRENT_TIMESTAMP
                        WHERE symbol IN ({placeholders})
                    """, batch)
                self.conn.commit()
            except Exception as e:
                print(f"[WARN] Deactivation failed: {e}")

        # Add new tickers to stocks_master (if not present)
        if new_tickers:
            added_count = 0
            for ticker in new_tickers:
                try:
                    # Senior Dev: stocks_master.company_name is NOT NULL and has no default.
                    # We must provide it or the INSERT OR IGNORE will fail silently.
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO stocks_master (symbol, company_name, is_active)
                        VALUES (?, ?, 1)
                        """,
                        (ticker, f"New Listing: {ticker}")
                    )
                    added_count += cursor.rowcount
                except Exception as e:
                    print(f"[WARN] Failed to insert {ticker}: {e}")
            self.conn.commit()
            if added_count:
                print(f"[LOG] Added {added_count} new tickers to stocks_master")

        # Update ISINs dynamically (bhavcopy/NSELIB)
        isin_updated = 0
        isin_update_samples = []
        try:
            # Detect ISIN column name
            isin_col = None
            for c in equity_df.columns:
                lc = str(c).strip().lower()
                if lc in ("isin", "isin number", "isin_number", "isinnumber"):
                    isin_col = c
                    break
            if isin_col:
                cursor = self.conn.cursor()
                for _, row in equity_df.iterrows():
                    sym = str(row.get('SYMBOL') or '').strip().upper()
                    isin_val = row.get(isin_col)
                    if not sym:
                        continue
                    if isin_val and str(isin_val).strip():
                        try:
                            cursor.execute("UPDATE stocks_master SET isin = ? WHERE symbol = ?", (str(isin_val).strip(), sym))
                            isin_updated += (1 if cursor.rowcount else 0)
                            if len(isin_update_samples) < 10:
                                isin_update_samples.append({"symbol": sym, "isin": str(isin_val).strip()})
                        except Exception:
                            pass
                self.conn.commit()
            # Fallback to NSELIB equity list for missing ISINs
            if NSELIB_AVAILABLE:
                try:
                    eq_df = capital_market.equity_list()
                    if isinstance(eq_df, pd.DataFrame) and not eq_df.empty:
                        # normalize headers
                        def _norm(s):
                            s = str(s).strip().lower()
                            if 'symbol' in s:
                                return 'SYMBOL'
                            if 'isin' in s:
                                return 'ISIN'
                            return s
                        eq_df = eq_df.rename(columns={c: _norm(c) for c in eq_df.columns})
                        if 'SYMBOL' in eq_df.columns and 'ISIN' in eq_df.columns:
                            cursor = self.conn.cursor()
                            for _, row in eq_df.iterrows():
                                sym = str(row.get('SYMBOL') or '').strip().upper()
                                isin_val = row.get('ISIN')
                                if not sym or not isin_val:
                                    continue
                                try:
                                    cursor.execute("UPDATE stocks_master SET isin = COALESCE(isin, ?) WHERE symbol = ?", (str(isin_val).strip(), sym))
                                except Exception:
                                    pass
                            self.conn.commit()
                except Exception:
                    pass
        except Exception:
            pass

        # Cache bhavcopy for future reference
        cache_file = self.cache_dir / f"bhavcopy_{date.strftime('%Y%m%d')}.csv"
        equity_df.to_csv(cache_file, index=False)

        # OHLC data already loaded at start of function


        # Write unified update log
        try:
            cursor.execute(
                """
                INSERT INTO download_log (table_name, symbol, status, records_added, error_message, timestamp)
                VALUES (?, NULL, ?, ?, NULL, CURRENT_TIMESTAMP)
                """,
                (
                    'stocks_master',
                    'success',
                    len(new_tickers) + isin_updated
                )
            )
        except Exception:
            pass

        matched_old = {tc['old_ticker'] for tc in ticker_changes}
        matched_new = {tc['new_ticker'] for tc in ticker_changes}
        unresolved_old = list(set(disappeared_tickers or set()) - matched_old)
        unresolved_new = list(set(new_tickers or set()) - matched_new)
        report = {
            'date': date.strftime('%Y-%m-%d'),
            'total_tickers': len(current_tickers),
            'new_tickers': list(new_tickers),
            'disappeared_tickers': list(disappeared_tickers),
            'ticker_changes': ticker_changes,  # Auto-detected ticker changes
            'detected_ipos': detected_ipos,  # Auto-detected IPO listings
            'data_source': data_source,
            'cache_file': str(cache_file),
            'ohlc_inserted': ohlc_stats['inserted'],
            'ohlc_skipped': ohlc_stats['skipped'],
            'ohlc_failed': ohlc_stats['failed'],
            'reactivated': to_reactivate if 'to_reactivate' in locals() else [],
            'isin_updates': isin_update_samples,
            'unresolved_disappeared': unresolved_old[:20],
            'unresolved_new': unresolved_new[:20]
        }
        print(f"[LOG] Reactivated: {len(report['reactivated'])} symbols")
        if report['reactivated']:
            print(f"[LOG] Reactivated sample: {report['reactivated'][:10]}")
        print(f"[LOG] ISIN updates: {len(report['isin_updates'])} samples")
        if report['isin_updates']:
            print(f"[LOG] ISIN sample: {report['isin_updates']}")
        if report['unresolved_disappeared']:
            print(f"[LOG] Unresolved disappeared: {report['unresolved_disappeared']}")
        if report['unresolved_new']:
            print(f"[LOG] Unresolved new: {report['unresolved_new']}")
        return report

    def _correlate_ticker_changes(
        self,
        disappeared: Set[str],
        new: Set[str],
        date: datetime,
        equity_df: pd.DataFrame
    ) -> List[Dict]:
        """
        Auto-correlate disappeared and new tickers to detect ticker changes

        Strategy:
        1. Check CF-CA for demergers on this date
        2. Check stock_aliases for name changes around this date
        3. Create ticker mappings when high confidence correlation found

        Returns:
            List of detected ticker changes with metadata
        """
        if not disappeared or not new:
            return []

        ticker_changes = []

        try:
            cursor = self.conn.cursor()
            if disappeared:
                ph = ','.join('?' * len(disappeared))
                cursor.execute(f"SELECT symbol, isin, company_name FROM stocks_master WHERE symbol IN ({ph})", list(disappeared))
                dis_rows = cursor.fetchall()
            else:
                dis_rows = []
            if new:
                ph2 = ','.join('?' * len(new))
                cursor.execute(f"SELECT symbol, isin, company_name FROM stocks_master WHERE symbol IN ({ph2})", list(new))
                new_rows = cursor.fetchall()
            else:
                new_rows = []
            dis_by_isin = {}
            for row in dis_rows:
                sym = row['symbol'] if isinstance(row, sqlite3.Row) else row[0]
                isin = row['isin'] if isinstance(row, sqlite3.Row) else row[1]
                dis_by_isin.setdefault(isin, []).append(sym)
            new_by_isin = {}
            for row in new_rows:
                sym = row['symbol'] if isinstance(row, sqlite3.Row) else row[0]
                isin = row['isin'] if isinstance(row, sqlite3.Row) else row[1]
                new_by_isin.setdefault(isin, []).append(sym)
            for isin, olds in dis_by_isin.items():
                if not isin:
                    continue
                news = new_by_isin.get(isin) or []
                for old_ticker in olds:
                    for new_ticker in news:
                        ticker_changes.append({
                            'old_ticker': old_ticker,
                            'new_ticker': new_ticker,
                            'change_date': date.strftime('%Y-%m-%d'),
                            'reason': 'isin_match',
                            'confidence': 95
                        })
                        # self._store_ticker_mapping(old_ticker, new_ticker, new_ticker, date, 'isin_match', 95)
                        print(f"[AUTO-DETECT] Ticker change: {old_ticker} -> {new_ticker} (isin_match)")

            csv_files = sorted(list(self.db_path.parent.glob('CF-CA-*.csv')))
            cf_path = csv_files[-1] if csv_files else None
            if cf_path:
                cf_df = pd.read_csv(cf_path)
                cf_df.columns = cf_df.columns.str.strip()
                purposes = ['Demerger', 'Scheme of Arrangement', 'Amalgamation', 'Merger']
                mask_purpose = cf_df['PURPOSE'].str.contains('|'.join(purposes), case=False, na=False)
                try:
                    ex_dates = pd.to_datetime(cf_df['EX-DATE'], errors='coerce', format='%d-%b-%Y')
                except Exception:
                    ex_dates = pd.to_datetime(cf_df['EX-DATE'], errors='coerce')
                cf_df = cf_df.assign(__ex_date=ex_dates)
                dmin = (date - timedelta(days=30))
                dmax = (date + timedelta(days=30))
                window = (cf_df['__ex_date'] >= dmin) & (cf_df['__ex_date'] <= dmax)
                cfd = cf_df[mask_purpose & window]
                for _, row in cfd.iterrows():
                    company_name = row['COMPANY NAME']
                    new_symbol = row['SYMBOL']
                    if new_symbol in new:
                        for old_ticker in disappeared:
                            nm = company_name or ''
                            if (old_ticker.lower() in nm.lower() or nm.lower()[:5] in old_ticker.lower()):
                                ticker_changes.append({
                                    'old_ticker': old_ticker,
                                    'new_ticker': new_symbol,
                                    'change_date': date.strftime('%Y-%m-%d'),
                                    'reason': 'cf_ca_window',
                                    'confidence': 90,
                                    'company_name': company_name
                                })
                                # self._store_ticker_mapping(old_ticker, new_symbol, company_name, date, 'cf_ca_window', 90)
                                print(f"[AUTO-DETECT] Ticker change: {old_ticker} -> {new_symbol} (cf_ca_window)")

            cursor.execute("SELECT old_name,new_name,nse_symbol,change_date,confidence FROM stock_aliases")
            alias_rows = cursor.fetchall()
            def _clean(s):
                x = str(s or '')
                x = re.sub(r'\s+(Ltd\.?|Limited|Private|Pvt\.?|Corporation|Corp\.?|Inc\.?)$', '', x, flags=re.IGNORECASE)
                x = re.sub(r'[&()\[\].,]', ' ', x)
                x = ' '.join(x.split())
                return x.strip().upper()
            for row in alias_rows:
                old_name = row['old_name'] if isinstance(row, sqlite3.Row) else row[0]
                new_name = row['new_name'] if isinstance(row, sqlite3.Row) else row[1]
                sym = row['nse_symbol'] if isinstance(row, sqlite3.Row) else row[2]
                chg = row['change_date'] if isinstance(row, sqlite3.Row) else row[3]
                try:
                    chg_dt = datetime.strptime(str(chg), '%Y-%m-%d')
                except Exception:
                    chg_dt = None
                if chg_dt:
                    if abs((chg_dt - date).days) > 30:
                        continue
                for old_ticker in disappeared:
                    if _clean(old_ticker) == _clean(old_name) or _clean(old_ticker) in _clean(new_name):
                        for new_ticker in new:
                            if sym and sym.upper() == new_ticker.upper():
                                ticker_changes.append({
                                    'old_ticker': old_ticker,
                                    'new_ticker': new_ticker,
                                    'change_date': date.strftime('%Y-%m-%d'),
                                    'reason': 'alias_recent',
                                    'confidence': 85
                                })
                                # self._store_ticker_mapping(old_ticker, new_ticker, new_name, date, 'alias_recent', 85)
                                print(f"[AUTO-DETECT] Ticker change: {old_ticker} -> {new_ticker} (alias_recent)")

        except Exception as e:
            print(f"[WARN] Auto-correlation failed: {e}")

        return ticker_changes

    def _store_ticker_mapping(
        self,
        old_ticker: str,
        new_ticker: str,
        company_name: str,
        change_date: datetime,
        reason: str,
        confidence: int
    ):
        """Store ticker mapping in stock_aliases table for future resolution"""
        cursor = self.conn.cursor()

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO stock_aliases
                (old_name, new_name, nse_symbol, change_date, confidence)
                VALUES (?, ?, ?, ?, ?)
            """, (
                old_ticker,
                company_name,
                new_ticker,
                change_date.strftime('%Y-%m-%d'),
                confidence / 100.0  # Store as decimal
            ))
            self.conn.commit()

        except Exception as e:
            print(f"[WARN] Failed to store ticker mapping: {e}")

    def _detect_ipo_listings(
        self,
        new_tickers: Set[str],
        ticker_changes: List[Dict],
        date: datetime,
        equity_df: pd.DataFrame
    ) -> List[Dict]:
        """
        Detect IPO listings from new tickers

        Strategy:
        1. Filter out tickers that are result of demerger/name change
        2. Check if ticker already exists in ipo_data (avoid duplicates)
        3. Extract listing details from bhavcopy
        4. Store in ipo_data table

        Returns:
            List of detected IPO listings with metadata
        """
        if not new_tickers:
            return []

        detected_ipos = []

        # Get set of new tickers that are from ticker changes (not IPOs)
        changed_tickers = {tc['new_ticker'] for tc in ticker_changes}

        # Filter to get truly new tickers (potential IPOs)
        potential_ipos = new_tickers - changed_tickers

        if not potential_ipos:
            return []

        try:
            cursor = self.conn.cursor()

            for symbol in potential_ipos:
                # Check if already in ipo_data table (avoid duplicates)
                cursor.execute("""
                    SELECT symbol FROM ipo_data WHERE symbol = ?
                """, (symbol,))

                if cursor.fetchone():
                    continue  # Already tracked as IPO

                # Get bhavcopy data for this symbol
                symbol_data = equity_df[equity_df['SYMBOL'] == symbol]

                if symbol_data.empty:
                    continue

                # Extract data (handle different column names)
                close_price = None
                company_name = symbol  # Default to symbol if no company name

                # Try to get close price from bhavcopy
                if 'CLOSE_PRICE' in symbol_data.columns:
                    close_price = symbol_data.iloc[0]['CLOSE_PRICE']
                elif 'ClsPric' in symbol_data.columns:
                    close_price = symbol_data.iloc[0]['ClsPric']
                elif 'CLOSE' in symbol_data.columns:
                    close_price = symbol_data.iloc[0]['CLOSE']

                # Try to get company name from stocks_master
                cursor.execute("""
                    SELECT company_name FROM stocks_master WHERE symbol = ?
                """, (symbol,))

                result = cursor.fetchone()
                if result and result['company_name']:
                    company_name = result['company_name']

                # Insert into ipo_data table
                # Note: issue_price is NULL for now (can be populated later from NSE IPO data)
                cursor.execute("""
                    INSERT OR IGNORE INTO ipo_data
                    (symbol, company_name, listing_date, listing_day_close, symbol_mapped)
                    VALUES (?, ?, ?, ?, 1)
                """, (
                    symbol,
                    company_name,
                    date.strftime('%Y-%m-%d'),
                    close_price
                ))

                self.conn.commit()

                detected_ipos.append({
                    'symbol': symbol,
                    'company_name': company_name,
                    'listing_date': date.strftime('%Y-%m-%d'),
                    'listing_day_close': close_price,
                    'note': f'Detected new IPO listing for {symbol}'
                })

                print(f"[IPO-DETECT] New IPO listing: {symbol} ({company_name}) on {date.strftime('%Y-%m-%d')}, Close: Rs.{close_price}")

        except Exception as e:
            print(f"[WARN] IPO detection failed: {e}")

        return detected_ipos

    def _load_tickers_for_date(self, date: datetime) -> Set[str]:
        """Load active tickers for a specific date from cache or database"""
        # Try cache first
        cache_file = self.cache_dir / f"bhavcopy_{date.strftime('%Y%m%d')}.csv"

        if cache_file.exists():
            try:
                df = pd.read_csv(cache_file)
                return set(df['SYMBOL'].unique())
            except:
                pass

        # Try database
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT new_tickers FROM bhavcopy_history
            WHERE date = ?
        """, (date.strftime('%Y-%m-%d'),))

        result = cursor.fetchone()
        if result and result['new_tickers']:
            return set(json.loads(result['new_tickers']))

        # Fallback: Load from stocks_master
        cursor.execute("SELECT symbol FROM stocks_master WHERE is_active = 1")
        return {row['symbol'] for row in cursor.fetchall()}

    def update_daily(self, date: Optional[datetime] = None) -> Dict:
        """
        Complete daily update workflow

        1. Download bhavcopy
        2. Process and compare with previous day
        3. Update database
        4. Return report

        Args:
            date: Date to process (defaults to today)

        Returns:
            Processing report dictionary
        """
        if date is None:
            date = datetime.now()

        print(f"\n{'='*70}")
        print(f"DAILY BHAVCOPY UPDATE: {date.strftime('%Y-%m-%d')}")
        print(f"{'='*70}")

        try:
            proc_date = date
            if proc_date is None:
                proc_date = datetime.now()
            
            # Check if already processed
            check = self.check_date_already_loaded(proc_date)
            if check['exists']:
                print(f"\n[INFO] OHLC data already loaded for {proc_date.strftime('%Y-%m-%d')}")
                print(f"       Records: {check['record_count']}")
                print(f"       Loaded at: {check['loaded_at']}")
                print(f"       Sample: {', '.join(check['symbols_sample'])}")
                print(f"\n       Use force=True to reload (via backfill), or skip to avoid duplicates.")
                return {
                    'date': proc_date.strftime('%Y-%m-%d'),
                    'status': 'already_loaded',
                    'ohlc_records': check['record_count']
                }

            df = None
            data_source = None
            try:
                df, data_source = self.download_bhavcopy(proc_date)
            except Exception as e:
                print(f"[WARN] Failed to download for {proc_date.strftime('%Y-%m-%d')}: {e}")
                print(f"[INFO] Trying previous trading days...")
                
                for i in range(1, 8):
                    try_date = proc_date - timedelta(days=i)
                    print(f"  Attempting {try_date.strftime('%Y-%m-%d')} (T-{i})...")
                    
                    # Check if fallback date already processed
                    check = self.check_date_already_loaded(try_date)
                    if check['exists']:
                        print(f"  [OK] Data for {try_date.strftime('%Y-%m-%d')} already exists. Stopping fallback.")
                        return {
                            'date': try_date.strftime('%Y-%m-%d'),
                            'status': 'already_loaded',
                            'ohlc_records': check['record_count']
                        }

                    try:
                        df, data_source = self.download_bhavcopy(try_date)
                        proc_date = try_date
                        print(f"  [OK] Found data for {try_date.strftime('%Y-%m-%d')}")
                        break
                    except Exception as err:
                        print(f"  [FAIL] Failed: {err}")
                        continue
            
            if df is None or data_source is None or len(df) == 0:
                raise Exception('no_data_after_backoff')

            # Process and update database
            result = self.process_bhavcopy(df, data_source, proc_date)

            # Print summary
            print(f"\n[SUMMARY]")
            print(f"  Total active tickers: {result['total_tickers']}")
            print(f"  New tickers: {len(result['new_tickers'])}")
            print(f"  Disappeared tickers: {len(result['disappeared_tickers'])}")
            print(f"  OHLC records inserted: {result['ohlc_inserted']}")
            print(f"  Ticker changes detected: {len(result['ticker_changes'])}")
            print(f"  IPO listings detected: {len(result['detected_ipos'])}")
            print(f"  Data source: {result['data_source']}")

            if result['ticker_changes']:
                print(f"\n[TICKER CHANGES] {len(result['ticker_changes'])} auto-detected:")
                for change in result['ticker_changes'][:5]:  # Show first 5
                    print(f"    {change['old_ticker']} -> {change['new_ticker']} (Reason: {change['reason']})")

            if result['detected_ipos']:
                print(f"\n[IPO LISTINGS] {len(result['detected_ipos'])} detected:")
                for ipo in result['detected_ipos'][:5]:  # Show first 5
                    print(f"    {ipo['symbol']} - {ipo['company_name']} (Close: Rs.{ipo['listing_day_close']})")

            if result['disappeared_tickers']:
                print(f"\n[ALERT] {len(result['disappeared_tickers'])} tickers disappeared (possible ticker changes):")
                for ticker in result['disappeared_tickers'][:10]:  # Show first 10
                    print(f"    - {ticker}")

            print(f"\n{'='*70}")

            return result

        except Exception as e:
            error_msg = f"Daily bhavcopy update failed: {e}"
            print(f"\n[ERROR] {error_msg}")

            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO download_log (table_name, symbol, status, records_added, error_message, timestamp)
                    VALUES (?, NULL, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        'bhavcopy',
                        'no_data',
                        0,
                        error_msg
                    )
                )
                self.conn.commit()
            except Exception:
                pass

            return {
                'date': date.strftime('%Y-%m-%d'),
                'error': error_msg,
                'status': 'failed',
                'reason': 'Data not available yet or download failed.',
                'next_action': 'Retry after market close (6:00 PM IST) or specify a past date manually'
            }


    def load_bhavcopy_to_ohlc(
        self, 
        df: pd.DataFrame, 
        date: datetime,
        batch_size: int = 500,
        validate: bool = True
    ) -> Dict:
        """
        Load bhavcopy OHLC data into daily_ohlc table with validation and bulk insert
        
        Args:
            df: Bhavcopy DataFrame
            date: Date of the bhavcopy
            batch_size: Number of records per batch (default: 500)
            validate: Whether to validate data (default: True)
            
        Returns:
            dict: Statistics of the operation
        """
        # Auto-detect column mapping
        col_mapping = BhavcopyColumnMapper.detect_columns(df)
        is_valid, error = BhavcopyColumnMapper.validate_required_columns(col_mapping)
        
        if not is_valid:
            print(f"[ERROR] Invalid bhavcopy format: {error}")
            return {'inserted': 0, 'skipped': 0, 'failed': 0}
        
        print(f"[INFO] Detected columns: {col_mapping}")
        
        # Filter to equity and book entry series
        if 'series' in col_mapping:
            series_col = col_mapping['series']
            equity_df = df[df[series_col].isin(['EQ', 'BE'])].copy()
        else:
            equity_df = df.copy()
            print(f"[WARN] No series column found, processing all rows as equity")
        
        print(f"[INFO] Processing {len(equity_df)} stocks (EQ + BE series) for {date.strftime('%Y-%m-%d')}")
        
        # Prepare standardized data
        records_to_insert = []
        stats = {
            'total': len(equity_df),
            'inserted': 0,
            'skipped': 0,
            'failed': 0,
            'validation_errors': [],
            'duplicate_symbols': []
        }
        
        for idx, row in equity_df.iterrows():
            # Map to standardized format
            try:
                standardized = {
                    'symbol': row[col_mapping['symbol']],
                    'date': date.strftime('%Y-%m-%d'),
                    'open': row[col_mapping['open']],
                    'high': row[col_mapping['high']],
                    'low': row[col_mapping['low']],
                    'close': row[col_mapping['close']],
                    'volume': row.get(col_mapping.get('volume'), 0),
                    'prev_close': row.get(col_mapping.get('prev_close')),
                    'data_source': 'bhavcopy'
                }
                
                # Validate if enabled
                if validate:
                    is_valid, error = OHLCValidator.validate_row(standardized)
                    if not is_valid:
                        stats['failed'] += 1
                        stats['validation_errors'].append({
                            'symbol': standardized['symbol'],
                            'error': error
                        })
                        continue
                
                records_to_insert.append(standardized)
            except Exception as e:
                stats['failed'] += 1
                continue
        
        # Bulk insert with batching
        cursor = self.conn.cursor()
        
        for i in range(0, len(records_to_insert), batch_size):
            batch = records_to_insert[i:i + batch_size]
            
            for record in batch:
                try:
                    cursor.execute('''
                        INSERT INTO daily_ohlc
                        (symbol, date, open, high, low, close, volume, prev_close, data_source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        record['symbol'],
                        record['date'],
                        record['open'],
                        record['high'],
                        record['low'],
                        record['close'],
                        record['volume'],
                        record['prev_close'],
                        record['data_source']
                    ))
                    stats['inserted'] += 1
                    
                except sqlite3.IntegrityError:
                    # Duplicate (symbol, date) - expected for reruns
                    stats['skipped'] += 1
                    stats['duplicate_symbols'].append(record['symbol'])
                
                except Exception as e:
                    stats['failed'] += 1
                    stats['validation_errors'].append({
                        'symbol': record['symbol'],
                        'error': str(e)
                    })
            
            # Commit batch
            self.conn.commit()
            
            # Progress feedback
            progress = min(i + batch_size, len(records_to_insert))
            if progress % 1000 == 0 or progress == len(records_to_insert):
                print(f"[PROGRESS] {progress}/{len(records_to_insert)} records processed "
                      f"(Inserted: {stats['inserted']}, Skipped: {stats['skipped']}, Failed: {stats['failed']})")
        
        # Summary
        print(f"\n[SUMMARY] OHLC Load Complete")
        print(f"  [OK] Inserted: {stats['inserted']} new records")
        if stats['skipped'] > 0:
            print(f"  ⊘ Skipped: {stats['skipped']} duplicates")
        if stats['failed'] > 0:
            print(f"  [FAIL] Failed: {stats['failed']} validation errors")
            if len(stats['validation_errors']) <= 5:
                print(f"  Errors:")
                for err in stats['validation_errors']:
                    print(f"    - {err['symbol']}: {err['error']}")
        
        return stats

    def check_date_already_loaded(self, date: datetime) -> Dict:
        """
        Check if OHLC data for a date is already loaded
        
        Returns:
            dict: Status of data for the date
        """
        cursor = self.conn.cursor()
        date_str = date.strftime('%Y-%m-%d')
        
        # Check daily_ohlc
        try:
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM daily_ohlc WHERE date = ?",
                (date_str,)
            )
            result = cursor.fetchone()
            ohlc_count = result['cnt'] if result else 0
        except Exception:
            ohlc_count = 0
        
        # Check bhavcopy_history
        try:
            cursor.execute(
                "SELECT download_timestamp FROM bhavcopy_history WHERE date = ?",
                (date_str,)
            )
            history = cursor.fetchone()
            loaded_at = history['download_timestamp'] if history else None
        except Exception:
            loaded_at = None
        
        # Get sample symbols
        symbols_sample = []
        if ohlc_count > 0:
            try:
                cursor.execute(
                    "SELECT symbol FROM daily_ohlc WHERE date = ? LIMIT 5",
                    (date_str,)
                )
                symbols_sample = [row['symbol'] for row in cursor.fetchall()]
            except Exception:
                pass
        
        return {
            'exists': ohlc_count > 0,
            'record_count': ohlc_count,
            'symbols_sample': symbols_sample,
            'loaded_at': loaded_at
        }

    def backfill_date_range(
        self,
        start_date: datetime,
        end_date: datetime,
        force: bool = False,
        skip_weekends: bool = True
    ) -> Dict:
        """
        Backfill OHLC data for a date range
        
        Args:
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
            force: If True, reprocess even if data exists
            skip_weekends: If True, skip Saturdays and Sundays
            
        Returns:
            dict: Summary of backfill operation
        """
        print(f"\n{'='*70}")
        print(f"BACKFILL: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        print(f"{'='*70}\n")
        
        current_date = start_date
        results = []
        
        while current_date <= end_date:
            # Skip weekends (market closed)
            if skip_weekends and current_date.weekday() >= 5:
                print(f"[SKIP] {current_date.strftime('%Y-%m-%d')} is a weekend")
                current_date += timedelta(days=1)
                continue
            
            # Check if already exists
            check = self.check_date_already_loaded(current_date)
            
            if check['exists'] and not force:
                print(f"[EXISTS] {current_date.strftime('%Y-%m-%d')} already loaded "
                      f"({check['record_count']} records at {check['loaded_at']})")
                results.append({
                    'date': current_date.strftime('%Y-%m-%d'),
                    'status': 'skipped',
                    'reason': 'already_exists'
                })
                current_date += timedelta(days=1)
                continue
            
            # If force mode and data exists, delete it first
            if force and check['exists']:
                print(f"[FORCE] Deleting {check['record_count']} existing records for {current_date.strftime('%Y-%m-%d')}")
                cursor = self.conn.cursor()
                cursor.execute("DELETE FROM daily_ohlc WHERE date = ?", (current_date.strftime('%Y-%m-%d'),))
                self.conn.commit()
            
            # Process this date
            try:
                result = self.update_daily(current_date)
                results.append({
                    'date': current_date.strftime('%Y-%m-%d'),
                    'status': 'success',
                    'ohlc_records': result.get('ohlc_inserted', 0)
                })
            except Exception as e:
                print(f"[ERROR] {current_date.strftime('%Y-%m-%d')}: {e}")
                results.append({
                    'date': current_date.strftime('%Y-%m-%d'),
                    'status': 'failed',
                    'error': str(e)
                })
            
            current_date += timedelta(days=1)
            time.sleep(1)  # Rate limiting
        
        # Summary
        print(f"\n{'='*70}")
        print(f"BACKFILL COMPLETE")
        print(f"{'='*70}")
        
        success_count = sum(1 for r in results if r['status'] == 'success')
        skipped_count = sum(1 for r in results if r['status'] == 'skipped')
        failed_count = sum(1 for r in results if r['status'] == 'failed')
        
        print(f"  [OK] Success: {success_count}")
        print(f"  ⊘ Skipped: {skipped_count}")
        print(f"  [FAIL] Failed: {failed_count}")
        
        return {
            'total_dates': len(results),
            'success': success_count,
            'skipped': skipped_count,
            'failed': failed_count,
            'details': results
        }

    def get_history(self, days: int = 30) -> pd.DataFrame:
        """Get bhavcopy history for last N days"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM bhavcopy_history
            ORDER BY date DESC
            LIMIT ?
        """, (days,))

        rows = cursor.fetchall()
        return pd.DataFrame([dict(row) for row in rows])

    def __del__(self):
        """Close database connection"""
        if hasattr(self, 'conn'):
            self.conn.close()


# Convenience function
def download_latest_bhavcopy(db_path: str, cache_dir: str = "cache/bhavcopy") -> Dict:
    """
    Download and process latest bhavcopy (convenience function)

    Args:
        db_path: Path to SQLite database
        cache_dir: Cache directory

    Returns:
        Processing report

    Example:
        >>> result = download_latest_bhavcopy('Database/stock_market_new.db')
        >>> print(f"Active tickers: {result['total_tickers']}")
    """
    downloader = BhavcopyDownloader(db_path, cache_dir)
    return downloader.update_daily()
