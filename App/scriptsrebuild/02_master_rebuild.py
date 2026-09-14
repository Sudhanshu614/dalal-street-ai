"""
PHASE 2: MASTER DATABASE REBUILD
Downloads ALL data using validated APIs

Data Sources (ALL VALIDATED):
- Stock Master: nselib equity_list() - 2,182 stocks in 3s
- Historical OHLCV: OpenChart 20+ years - ~60 min
- Fundamentals: Screener.in + PB calculation - ~72 min
- Corporate Actions: nselib - 2s

Expected Time: 2-3 hours
Expected Records: 10-15M

Usage:
    python scripts/rebuild/02_master_rebuild.py [--test]

    --test: Test mode (5 stocks only)
"""

import sqlite3
import time
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
import re

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Imports
try:
    from openchart import NSEData
    from nselib import capital_market
except ImportError as e:
    print(f"[ERROR] Missing dependency: {e}")
    print("Install: pip install openchart")
    print("Install nselib from local: cd nselib-2.0 && pip install -e .")
    sys.exit(1)

# Configuration
DB_FILE = Path(__file__).parent.parent.parent / "database" / "stock_market_new.db"
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / f"02_rebuild_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# Statistics
stats = {
    'stock_master': {'total': 0, 'success': 0, 'failed': 0},
    'historical_ohlcv': {'total': 0, 'success': 0, 'failed': 0, 'records': 0},
    'fundamentals': {'total': 0, 'success': 0, 'failed': 0},
    'corporate_actions': {'records': 0},
}

def log_message(message, console=True, file=True):
    """Log to console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"

    if console:
        try:
            print(log_entry)
        except:
            print(log_entry.encode('ascii', 'replace').decode('ascii'))

    if file:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_entry + '\n')

def log_download(conn, table_name, symbol, status, records=0, error=None):
    """Log download attempt to database"""
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO download_log (table_name, symbol, status, records_added, error_message)
        VALUES (?, ?, ?, ?, ?)
    ''', (table_name, symbol, status, records, error))
    conn.commit()

# ============================================================================
# PHASE 2.1: STOCK MASTER (nselib equity_list)
# ============================================================================

def download_stock_master(conn, test_mode=False):
    """
    Download stock master from nselib
    Source: nselib.capital_market.equity_list()
    Time: 3 seconds
    """
    log_message("="*70)
    log_message("PHASE 2.1: STOCK MASTER")
    log_message("="*70)

    try:
        log_message("[INFO] Fetching equity list from nselib...")
        start_time = time.time()

        equity_list = capital_market.equity_list()
        elapsed = time.time() - start_time

        log_message(f"[OK] Got {len(equity_list)} stocks in {elapsed:.2f}s")

        if test_mode:
            equity_list = equity_list.head(5)
            log_message(f"[TEST] Using only {len(equity_list)} stocks")

        stats['stock_master']['total'] = len(equity_list)

        cursor = conn.cursor()

        for idx, row in equity_list.iterrows():
            symbol = row['SYMBOL']
            company_name = row['NAME OF COMPANY']
            listing_date = row.get(' DATE OF LISTING', None)
            face_value = row.get(' FACE VALUE', None)

            try:
                cursor.execute('''
                    INSERT INTO stocks_master
                    (symbol, company_name, listing_date, face_value, series, is_active)
                    VALUES (?, ?, ?, ?, ?, 1)
                ''', (symbol, company_name, listing_date, face_value, 'EQ'))

                stats['stock_master']['success'] += 1

            except sqlite3.IntegrityError as e:
                stats['stock_master']['failed'] += 1
                log_message(f"[WARN] {symbol}: {e}")

        conn.commit()

        log_message("")
        log_message(f"[SUMMARY] Stock Master:")
        log_message(f"  Success: {stats['stock_master']['success']}")
        log_message(f"  Failed: {stats['stock_master']['failed']}")
        log_message("")

        log_download(conn, 'stocks_master', None, 'SUCCESS', stats['stock_master']['success'])

        return True

    except Exception as e:
        log_message(f"[ERROR] Stock master download failed: {e}")
        log_download(conn, 'stocks_master', None, 'FAILED', 0, str(e))
        return False

# ============================================================================
# PHASE 2.2: HISTORICAL OHLCV (OpenChart)
# ============================================================================

def download_historical_ohlcv(conn):
    """
    Download 20+ years of OHLCV data
    Source: OpenChart nse.historical()
    Time: ~1.4s per stock = 60 min total
    """
    log_message("="*70)
    log_message("PHASE 2.2: HISTORICAL OHLCV")
    log_message("="*70)

    try:
        # Initialize OpenChart
        log_message("[INFO] Initializing OpenChart...")
        nse = NSEData()

        log_message("[INFO] Downloading stock master data...")
        nse.download()
        log_message("[OK] OpenChart ready")

        # Get symbols from database
        cursor = conn.cursor()
        cursor.execute("SELECT symbol FROM stocks_master WHERE is_active = 1")
        symbols = [row[0] for row in cursor.fetchall()]

        stats['historical_ohlcv']['total'] = len(symbols)

        log_message(f"[INFO] Downloading 20 years of data for {len(symbols)} stocks")
        log_message(f"[INFO] Estimated time: {len(symbols) * 1.4 / 60:.1f} minutes")
        log_message("")

        # Calculate date range (20 years)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=20*365)

        for idx, symbol in enumerate(symbols, 1):
            try:
                start_time = time.time()

                # Get historical data
                data = nse.historical(
                    symbol=symbol,
                    exchange='NSE',
                    start=start_date,
                    end=end_date,
                    interval='1d'
                )

                elapsed = time.time() - start_time

                if data is not None and len(data) > 0:
                    # Insert data
                    for date, row in data.iterrows():
                        try:
                            cursor.execute('''
                                INSERT INTO daily_ohlc
                                (symbol, date, open, high, low, close, volume)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                symbol,
                                date.strftime('%Y-%m-%d'),
                                row['Open'],
                                row['High'],
                                row['Low'],
                                row['Close'],
                                row.get('Volume', 0)
                            ))
                        except sqlite3.IntegrityError:
                            pass  # Duplicate, skip

                    conn.commit()

                    stats['historical_ohlcv']['success'] += 1
                    stats['historical_ohlcv']['records'] += len(data)

                    log_message(f"[{idx}/{len(symbols)}] {symbol}: {len(data)} records in {elapsed:.2f}s")
                    log_download(conn, 'daily_ohlc', symbol, 'SUCCESS', len(data))

                else:
                    stats['historical_ohlcv']['failed'] += 1
                    log_message(f"[{idx}/{len(symbols)}] {symbol}: No data")
                    log_download(conn, 'daily_ohlc', symbol, 'FAILED', 0, 'No data returned')

            except Exception as e:
                stats['historical_ohlcv']['failed'] += 1
                log_message(f"[{idx}/{len(symbols)}] {symbol}: Error - {e}")
                log_download(conn, 'daily_ohlc', symbol, 'FAILED', 0, str(e))

            # Progress update every 50 stocks
            if idx % 50 == 0:
                log_message(f"\n[PROGRESS] {idx}/{len(symbols)} stocks processed")
                log_message(f"  Success: {stats['historical_ohlcv']['success']}")
                log_message(f"  Records: {stats['historical_ohlcv']['records']:,}")
                log_message(f"  Failed: {stats['historical_ohlcv']['failed']}\n")

            time.sleep(0.5)  # Rate limiting

        log_message("")
        log_message(f"[SUMMARY] Historical OHLCV:")
        log_message(f"  Success: {stats['historical_ohlcv']['success']}")
        log_message(f"  Failed: {stats['historical_ohlcv']['failed']}")
        log_message(f"  Total Records: {stats['historical_ohlcv']['records']:,}")
        log_message("")

        return True

    except Exception as e:
        log_message(f"[ERROR] Historical OHLCV download failed: {e}")
        import traceback
        log_message(traceback.format_exc())
        return False

# ============================================================================
# PHASE 2.3: FUNDAMENTALS (Screener.in)
# ============================================================================

def parse_number(value_str):
    """Parse number from Screener.in format"""
    if not value_str or value_str.strip() in ['', '-', 'N/A']:
        return None

    try:
        cleaned = re.sub(r'[₹,\s%Cr]', '', str(value_str).strip())
        return float(cleaned)
    except:
        return None

def scrape_screener(symbol):
    """Scrape fundamental data from Screener.in"""
    url = f"https://www.screener.in/company/{symbol}/consolidated/"
    headers = {'User-Agent': 'Mozilla/5.0'}

    try:
        response = requests.get(url, headers=headers, timeout=15)

        if response.status_code == 404:
            return None, "not_found"

        if response.status_code != 200:
            return None, f"http_{response.status_code}"

        soup = BeautifulSoup(response.text, 'html.parser')

        data = {'symbol': symbol}

        # Parse top ratios
        top_ratios = soup.find('ul', {'id': 'top-ratios'})
        if top_ratios:
            for li in top_ratios.find_all('li'):
                name = li.find('span', class_='name')
                number = li.find('span', class_='number')

                if name and number:
                    field = name.get_text(strip=True)
                    value = number.get_text(strip=True)

                    if field == 'Market Cap':
                        data['market_cap'] = parse_number(value)
                    elif field == 'Current Price':
                        data['current_price'] = parse_number(value)
                    elif field == 'High / Low':
                        parts = value.split('/')
                        if len(parts) == 2:
                            data['week52_high'] = parse_number(parts[0])
                            data['week52_low'] = parse_number(parts[1])
                    elif field == 'Stock P/E':
                        data['pe_ratio'] = parse_number(value)
                    elif field == 'Book Value':
                        data['book_value'] = parse_number(value)
                    elif field == 'Dividend Yield':
                        data['dividend_yield'] = parse_number(value)
                    elif field == 'ROCE':
                        data['roce'] = parse_number(value)
                    elif field == 'ROE':
                        data['roe'] = parse_number(value)
                    elif field == 'Face Value':
                        data['face_value'] = parse_number(value)

        # Calculate PB Ratio (NOT directly available)
        if data.get('current_price') and data.get('book_value') and data['book_value'] > 0:
            data['pb_ratio'] = data['current_price'] / data['book_value']

        # Parse shareholding
        sh_section = soup.find('section', {'id': 'shareholding'})
        if sh_section:
            table = sh_section.find('table', class_='data-table')
            if table:
                rows = table.find('tbody').find_all('tr')
                for row in rows:
                    cells = row.find_all('td')
                    if cells:
                        holder = cells[0].get_text(strip=True)
                        if 'Promoter' in holder:
                            data['promoter_holding'] = parse_number(cells[1].get_text(strip=True))
                        elif 'FII' in holder:
                            data['fii_holding'] = parse_number(cells[1].get_text(strip=True))
                        elif 'DII' in holder:
                            data['dii_holding'] = parse_number(cells[1].get_text(strip=True))

        return data, None

    except Exception as e:
        return None, str(e)

def download_fundamentals(conn):
    """
    Download fundamental data from Screener.in
    Time: ~2s per stock = 72 min total
    """
    log_message("="*70)
    log_message("PHASE 2.3: FUNDAMENTALS")
    log_message("="*70)

    cursor = conn.cursor()
    cursor.execute("SELECT symbol FROM stocks_master WHERE is_active = 1")
    symbols = [row[0] for row in cursor.fetchall()]

    stats['fundamentals']['total'] = len(symbols)

    log_message(f"[INFO] Scraping fundamentals for {len(symbols)} stocks")
    log_message(f"[INFO] Estimated time: {len(symbols) * 2 / 60:.1f} minutes")
    log_message("")

    for idx, symbol in enumerate(symbols, 1):
        try:
            data, error = scrape_screener(symbol)

            if data:
                cursor.execute('''
                    INSERT INTO fundamentals
                    (symbol, market_cap, current_price, week52_high, week52_low,
                     pe_ratio, pb_ratio, book_value, face_value, dividend_yield,
                     roe, roce, promoter_holding, fii_holding, dii_holding,
                     last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data['symbol'],
                    data.get('market_cap'),
                    data.get('current_price'),
                    data.get('week52_high'),
                    data.get('week52_low'),
                    data.get('pe_ratio'),
                    data.get('pb_ratio'),
                    data.get('book_value'),
                    data.get('face_value'),
                    data.get('dividend_yield'),
                    data.get('roe'),
                    data.get('roce'),
                    data.get('promoter_holding'),
                    data.get('fii_holding'),
                    data.get('dii_holding'),
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))

                conn.commit()

                stats['fundamentals']['success'] += 1
                log_message(f"[{idx}/{len(symbols)}] {symbol}: OK")
                log_download(conn, 'fundamentals', symbol, 'SUCCESS', 1)

            else:
                stats['fundamentals']['failed'] += 1
                log_message(f"[{idx}/{len(symbols)}] {symbol}: {error}")
                log_download(conn, 'fundamentals', symbol, 'FAILED', 0, error)

        except Exception as e:
            stats['fundamentals']['failed'] += 1
            log_message(f"[{idx}/{len(symbols)}] {symbol}: Error - {e}")
            log_download(conn, 'fundamentals', symbol, 'FAILED', 0, str(e))

        if idx % 50 == 0:
            log_message(f"\n[PROGRESS] {idx}/{len(symbols)}")
            log_message(f"  Success: {stats['fundamentals']['success']}")
            log_message(f"  Failed: {stats['fundamentals']['failed']}\n")

        time.sleep(2)  # Rate limiting

    log_message("")
    log_message(f"[SUMMARY] Fundamentals:")
    log_message(f"  Success: {stats['fundamentals']['success']}")
    log_message(f"  Failed: {stats['fundamentals']['failed']}")
    log_message("")

    return True

# ============================================================================
# PHASE 2.4: CORPORATE ACTIONS (nselib)
# ============================================================================

def download_corporate_actions(conn):
    """
    Download corporate actions from nselib
    Time: 2-3 seconds for 10+ years
    """
    log_message("="*70)
    log_message("PHASE 2.4: CORPORATE ACTIONS")
    log_message("="*70)

    try:
        # Get 10 years of actions (2015-2025)
        from_date = '01-01-2015'
        to_date = datetime.now().strftime('%d-%m-%Y')

        log_message(f"[INFO] Fetching corporate actions from {from_date} to {to_date}")

        start_time = time.time()
        actions = capital_market.corporate_actions_for_equity(
            from_date=from_date,
            to_date=to_date
        )
        elapsed = time.time() - start_time

        log_message(f"[OK] Got {len(actions)} actions in {elapsed:.2f}s")

        cursor = conn.cursor()

        for _, row in actions.iterrows():
            try:
                cursor.execute('''
                    INSERT INTO corporate_actions
                    (symbol, action_type, subject, ex_date, record_date,
                     bc_start_date, bc_end_date, face_value)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    row.get('symbol'),
                    row.get('ind'),
                    row.get('subject'),
                    row.get('exDate'),
                    row.get('recDate'),
                    row.get('bcStartDate'),
                    row.get('bcEndDate'),
                    row.get('faceVal')
                ))

                stats['corporate_actions']['records'] += 1

            except sqlite3.IntegrityError:
                pass  # Duplicate

        conn.commit()

        log_message(f"[SUMMARY] Corporate Actions: {stats['corporate_actions']['records']} records")
        log_download(conn, 'corporate_actions', None, 'SUCCESS', stats['corporate_actions']['records'])

        return True

    except Exception as e:
        log_message(f"[ERROR] Corporate actions failed: {e}")
        log_download(conn, 'corporate_actions', None, 'FAILED', 0, str(e))
        return False

# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true', help='Test mode (5 stocks only)')
    args = parser.parse_args()

    log_message("="*70)
    log_message("MASTER DATABASE REBUILD")
    log_message("="*70)
    log_message(f"Mode: {'TEST (5 stocks)' if args.test else 'PRODUCTION (all stocks)'}")
    log_message(f"Database: {DB_FILE}")
    log_message(f"Log: {LOG_FILE}")
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found. Run 01_create_schema.py first")
        return

    conn = sqlite3.connect(DB_FILE)

    start_time = time.time()

    try:
        # Phase 2.1: Stock Master
        if not download_stock_master(conn, test_mode=args.test):
            log_message("[ABORT] Stock master failed")
            return

        # Phase 2.2: Historical OHLCV
        if not args.test:  # Skip in test mode (takes too long)
            if not download_historical_ohlcv(conn):
                log_message("[WARN] Historical OHLCV failed, continuing...")

        # Phase 2.3: Fundamentals
        if not download_fundamentals(conn):
            log_message("[WARN] Fundamentals failed, continuing...")

        # Phase 2.4: Corporate Actions
        if not download_corporate_actions(conn):
            log_message("[WARN] Corporate actions failed, continuing...")

        # Final summary
        elapsed = time.time() - start_time

        log_message("")
        log_message("="*70)
        log_message("REBUILD COMPLETE!")
        log_message("="*70)
        log_message(f"Time: {elapsed/60:.1f} minutes")
        log_message("")
        log_message("Stock Master:")
        log_message(f"  Success: {stats['stock_master']['success']}")
        log_message(f"  Failed: {stats['stock_master']['failed']}")
        log_message("")
        log_message("Historical OHLCV:")
        log_message(f"  Success: {stats['historical_ohlcv']['success']}")
        log_message(f"  Records: {stats['historical_ohlcv']['records']:,}")
        log_message(f"  Failed: {stats['historical_ohlcv']['failed']}")
        log_message("")
        log_message("Fundamentals:")
        log_message(f"  Success: {stats['fundamentals']['success']}")
        log_message(f"  Failed: {stats['fundamentals']['failed']}")
        log_message("")
        log_message("Corporate Actions:")
        log_message(f"  Records: {stats['corporate_actions']['records']}")
        log_message("")
        log_message(f"Log file: {LOG_FILE}")
        log_message("")
        log_message("Next: Run 03_validate_database.py to check quality")
        log_message("="*70)

    except KeyboardInterrupt:
        log_message("\n[INTERRUPTED] Rebuild interrupted by user")
        log_message("Progress saved. Re-run to resume.")

    except Exception as e:
        log_message(f"\n[ERROR] Fatal error: {e}")
        import traceback
        log_message(traceback.format_exc())

    finally:
        conn.close()

if __name__ == "__main__":
    main()
