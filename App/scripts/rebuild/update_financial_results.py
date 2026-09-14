"""
MODERNIZED FINANCIAL RESULTS UPDATER
Scrapes quarterly and annual financial data from Screener.in

Senior Dev Improvements:
- No company_id dependency (uses direct symbol URLs)
- Incremental updates only (new + stale companies)
- Correct database path (stock_market_new.db)
- Integrated logging via download_log
- Schema validation before scraping
- Resume capability built-in
- Failure tracking and retry logic

Usage:
    python update_financial_results.py --mode quarterly --test
    python update_financial_results.py --mode annual --symbols RELIANCE,TCS
    python update_financial_results.py --mode quarterly --dry-run
    python update_financial_results.py --mode quarterly --stale-days 90
"""

import sqlite3
import requests
import re
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from bs4 import BeautifulSoup

# Configuration
SCRIPT_DIR = Path(__file__).resolve().parent
DB_FILE = SCRIPT_DIR.parent.parent / 'database' / 'stock_market_new.db'

BASE_URL = "https://www.screener.in/company/{symbol}/consolidated/"
USER_AGENT = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

RATE_LIMIT_DELAY = 2  # seconds between requests
MAX_RETRIES = 3
RETRY_DELAY = 5


def log_message(message):
    """Log with timestamp"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] {message}")


def clean_number(value_str):
    """Parse Indian number format (e.g., '1,234.56 Cr.' → 1234.56)"""
    if not value_str or value_str.strip() in ['', '-', 'N/A', 'n.a.']:
        return None
    try:
        cleaned = value_str.replace('₹', '').replace(',', '').replace('%', '')
        cleaned = cleaned.replace('Cr', '').replace('cr', '').strip()
        return float(cleaned)
    except:
        return None


def parse_quarter_date(quarter_str):
    """Convert 'Mar 2024' to '2024-03-31'"""
    try:
        month_map = {
            'Jan': ('01', '31'), 'Feb': ('02', '28'), 'Mar': ('03', '31'),
            'Apr': ('04', '30'), 'May': ('05', '31'), 'Jun': ('06', '30'),
            'Jul': ('07', '31'), 'Aug': ('08', '31'), 'Sep': ('09', '30'),
            'Oct': ('10', '31'), 'Nov': ('11', '30'), 'Dec': ('12', '31')
        }
        parts = quarter_str.strip().split()
        if len(parts) == 2:
            month_str, year = parts
            if month_str in month_map:
                month, day = month_map[month_str]
                return f"{year}-{month}-{day}"
        return None
    except:
        return None


def validate_database():
    """Validate database has required tables and columns"""
    log_message("Validating database schema...")
    
    if not DB_FILE.exists():
        raise FileNotFoundError(f"Database not found: {DB_FILE}")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Check required tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    
    required_tables = ['fundamentals', 'quarterly_results', 'annual_financials', 'download_log']
    missing_tables = [t for t in required_tables if t not in tables]
    
    if missing_tables:
        conn.close()
        raise ValueError(f"Missing required tables: {missing_tables}")
    
    # Validate quarterly_results schema
    cursor.execute("PRAGMA table_info(quarterly_results)")
    qr_columns = [row[1] for row in cursor.fetchall()]
    required_qr = ['symbol', 'quarter', 'sales', 'net_profit', 'eps']
    
    missing_qr = [c for c in required_qr if c not in qr_columns]
    if missing_qr:
        conn.close()
        raise ValueError(f"quarterly_results missing columns: {missing_qr}")
    
    # Validate annual_financials schema
    cursor.execute("PRAGMA table_info(annual_financials)")
    af_columns = [row[1] for row in cursor.fetchall()]
    required_af = ['symbol', 'year', 'sales', 'net_profit', 'eps']
    
    missing_af = [c for c in required_af if c not in af_columns]
    if missing_af:
        conn.close()
        raise ValueError(f"annual_financials missing columns: {missing_af}")
    
    conn.close()
    log_message("✓ Database schema validated")
    return True


def get_symbols_to_update(mode, stale_days=90, force_symbols=None):
    """
    Get list of symbols that need updating
    
    Returns symbols that are:
    1. New (in fundamentals but not in results table)
    2. Stale (last_updated > stale_days ago)
    3. Force-specified (command line override)
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if force_symbols:
        # User specified symbols directly
        symbols = force_symbols
        log_message(f"Using force-specified symbols: {symbols}")
        conn.close()
        return symbols
    
    table = 'quarterly_results' if mode == 'quarterly' else 'annual_financials'
    
    # 1. Find NEW companies (in fundamentals but not in results table)
    cursor.execute(f"""
        SELECT symbol FROM fundamentals 
        WHERE symbol NOT IN (SELECT DISTINCT symbol FROM {table})
        AND symbol IN (SELECT symbol FROM stocks_master WHERE is_active = 1)
    """)
    new_symbols = [row[0] for row in cursor.fetchall()]
    
    # 2. Find STALE companies (outdated data)
    stale_date = (datetime.now() - timedelta(days=stale_days)).strftime('%Y-%m-%d')
    cursor.execute(f"""
        SELECT DISTINCT symbol FROM {table}
        WHERE last_updated < ?
        AND symbol IN (SELECT symbol FROM stocks_master WHERE is_active = 1)
    """, (stale_date,))
    stale_symbols = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    
    # Combine and deduplicate
    all_symbols = list(set(new_symbols + stale_symbols))
    
    log_message(f"Found {len(new_symbols)} new companies")
    log_message(f"Found {len(stale_symbols)} stale companies (>{stale_days} days)")
    log_message(f"Total to update: {len(all_symbols)}")
    
    return all_symbols


def scrape_quarterly_data(symbol):
    """Scrape quarterly results from Screener.in"""
    url = BASE_URL.format(symbol=symbol)
    
    try:
        response = requests.get(url, headers=USER_AGENT, timeout=15)
        
        if response.status_code == 404:
            return None, "NOT_FOUND"
        
        if response.status_code != 200:
            return None, f"HTTP_{response.status_code}"
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find quarterly section
        section = soup.find('section', {'id': 'quarters'})
        if not section:
            return None, "NO_QUARTERLY_SECTION"
        
        table = section.find('table', class_='data-table')
        if not table:
            return None, "NO_TABLE"
        
        # Parse headers (quarters)
        headers = table.find('thead').find_all('th')
        quarters = [th.get_text(strip=True) for th in headers[1:]]
        
        if not quarters:
            return None, "NO_QUARTERS"
        
        # Parse rows
        quarterly_data = {q: {} for q in quarters}
        tbody = table.find('tbody')
        
        if tbody:
            rows = tbody.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) < 2:
                    continue
                
                metric_name = cells[0].get_text(strip=True).replace('+', '').strip()
                
                # Field mapping
                field_mapping = {
                    'Sales': 'sales',
                    'Other Income': 'other_income',
                    'Expenses': 'expenses',
                    'Operating Profit': 'operating_profit',
                    'OPM %': 'opm_percent',
                    'Interest': 'interest',
                    'Depreciation': 'depreciation',
                    'Profit before tax': 'profit_before_tax',
                    'Tax %': 'tax_percent',
                    'Net Profit': 'net_profit',
                    'EPS in Rs': 'eps',
                    'EPS': 'eps'
                }
                
                if metric_name in field_mapping:
                    field = field_mapping[metric_name]
                    for i, cell in enumerate(cells[1:]):
                        if i < len(quarters):
                            quarter = quarters[i]
                            value = clean_number(cell.get_text(strip=True))
                            quarterly_data[quarter][field] = value
        
        return quarterly_data, None
        
    except requests.Timeout:
        return None, "TIMEOUT"
    except Exception as e:
        return None, f"ERROR: {str(e)}"


def scrape_annual_data(symbol):
    """Scrape annual financials from Screener.in"""
    url = BASE_URL.format(symbol=symbol)
    
    try:
        response = requests.get(url, headers=USER_AGENT, timeout=15)
        
        if response.status_code == 404:
            return None, "NOT_FOUND"
        
        if response.status_code != 200:
            return None, f"HTTP_{response.status_code}"
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Helper function to scrape tables
        def scrape_table(section_id, field_mapping):
            section = soup.find('section', {'id': section_id})
            if not section:
                return {}
            
            table = section.find('table', class_='data-table')
            if not table:
                return {}
            
            headers = table.find('thead').find_all('th')
            years = [th.get_text(strip=True) for th in headers[1:]]
            
            if not years:
                return {}
            
            annual_data = {year: {} for year in years}
            tbody = table.find('tbody')
            
            if tbody:
                rows = tbody.find_all('tr')
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) < 2:
                        continue
                    
                    metric_name = cells[0].get_text(strip=True).replace('+', '').strip()
                    
                    if metric_name in field_mapping:
                        field = field_mapping[metric_name]
                        for i, cell in enumerate(cells[1:]):
                            if i < len(years):
                                year = years[i]
                                value = clean_number(cell.get_text(strip=True))
                                annual_data[year][field] = value
            
            return annual_data
        
        # 1. Profit & Loss
        pl_mapping = {
            'Sales': 'sales',
            'Expenses': 'expenses',
            'Operating Profit': 'operating_profit',
            'OPM %': 'opm_percent',
            'Other Income': 'other_income',
            'Interest': 'interest',
            'Depreciation': 'depreciation',
            'Profit before tax': 'profit_before_tax',
            'Tax %': 'tax_percent',
            'Net Profit': 'net_profit',
            'EPS in Rs': 'eps',
            'EPS': 'eps'
        }
        pl_data = scrape_table('profit-loss', pl_mapping)
        
        # 2. Balance Sheet
        bs_mapping = {
            'Equity Capital': 'equity_capital',
            'Reserves': 'reserves',
            'Borrowings': 'borrowings',
            'Fixed Assets': 'fixed_assets',
            'Total Assets': 'total_assets'
        }
        bs_data = scrape_table('balance-sheet', bs_mapping)
        
        # 3. Ratios
        ratios_mapping = {
            'ROE': 'roe',
            'ROCE': 'roce',
            'Debt to equity': 'debt_to_equity'
        }
        ratios_data = scrape_table('ratios', ratios_mapping)
        
        # 4. Shareholding (filter to March only for annual)
        sh_mapping = {
            'Promoters': 'promoter_holding',
            'FII': 'fii_holding',
            'DII': 'dii_holding'
        }
        sh_data_raw = scrape_table('shareholding', sh_mapping)
        sh_data = {year: data for year, data in sh_data_raw.items() if 'Mar' in year}
        
        # Merge all data
        all_years = set()
        for data in [pl_data, bs_data, ratios_data, sh_data]:
            all_years.update(data.keys())
        
        # Filter out TTM and keep only valid years
        filtered_years = {year for year in all_years if 'TTM' not in year.upper()}
        
        merged_data = {}
        for year in filtered_years:
            merged_data[year] = {}
            for data in [pl_data, bs_data, ratios_data, sh_data]:
                if year in data:
                    merged_data[year].update(data[year])
        
        return merged_data, None
        
    except requests.Timeout:
        return None, "TIMEOUT"
    except Exception as e:
        return None, f"ERROR: {str(e)}"


def save_quarterly_data(symbol, data, dry_run=False):
    """Save quarterly data to database"""
    if dry_run:
        log_message(f"  [DRY-RUN] Would save {len(data)} quarters")
        return len(data)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    saved_count = 0
    
    for quarter, values in data.items():
        if not values or all(v is None for v in values.values()):
            continue
        
        quarter_date = parse_quarter_date(quarter)
        
        cursor.execute('''
            INSERT OR REPLACE INTO quarterly_results
            (symbol, quarter, quarter_date,
             sales, other_income, expenses, operating_profit, opm_percent,
             interest, depreciation, profit_before_tax, tax_percent, net_profit, eps,
             last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            symbol, quarter, quarter_date,
            values.get('sales'), values.get('other_income'), values.get('expenses'),
            values.get('operating_profit'), values.get('opm_percent'),
            values.get('interest'), values.get('depreciation'),
            values.get('profit_before_tax'), values.get('tax_percent'),
            values.get('net_profit'), values.get('eps'),
            last_updated
        ))
        saved_count += 1
    
    conn.commit()
    conn.close()
    return saved_count


def save_annual_data(symbol, data, dry_run=False):
    """Save annual data to database"""
    if dry_run:
        log_message(f"  [DRY-RUN] Would save {len(data)} years")
        return len(data)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    saved_count = 0
    
    for year, values in data.items():
        if not values or all(v is None for v in values.values()):
            continue
        
        year_end_date = parse_quarter_date(year)
        
        cursor.execute('''
            INSERT OR REPLACE INTO annual_financials
            (symbol, year, year_end_date,
             sales, expenses, operating_profit,
             other_income, interest, depreciation,
             profit_before_tax, tax, net_profit, eps,
             equity_capital, reserves, borrowings, fixed_assets, total_assets,
             last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            symbol, year, year_end_date,
            values.get('sales'), values.get('expenses'), values.get('operating_profit'),
            values.get('other_income'), values.get('interest'),
            values.get('depreciation'), values.get('profit_before_tax'), values.get('tax_percent'),
            values.get('net_profit'), values.get('eps'),
            values.get('equity_capital'), values.get('reserves'), values.get('borrowings'),
            values.get('fixed_assets'), values.get('total_assets'),
            last_updated
        ))
        saved_count += 1
    
    conn.commit()
    conn.close()
    return saved_count


def log_result(symbol, table_name, status, records_added, error=None):
    """Log scrape result to download_log"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO download_log 
            (table_name, symbol, status, records_added, error_message, timestamp)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (table_name, symbol, status, records_added, error))
        conn.commit()
    except:
        pass  # Don't fail the whole operation if logging fails
    finally:
        conn.close()


def process_symbol(symbol, mode, dry_run=False):
    """Process a single symbol"""
    table_name = 'quarterly_results' if mode == 'quarterly' else 'annual_financials'
    
    # Scrape with retry
    for attempt in range(1, MAX_RETRIES + 1):
        if mode == 'quarterly':
            data, error = scrape_quarterly_data(symbol)
        else:
            data, error = scrape_annual_data(symbol)
        
        if data is not None:
            break
        
        if attempt < MAX_RETRIES:
            log_message(f"  Retry {attempt}/{MAX_RETRIES} after {error}")
            time.sleep(RETRY_DELAY)
    
    if data is None:
        log_result(symbol, table_name, 'failed', 0, error)
        return False, error
    
    if not data:
        log_result(symbol, table_name, 'no_data', 0, "No data found")
        return False, "NO_DATA"
    
    # Save
    if mode == 'quarterly':
        saved_count = save_quarterly_data(symbol, data, dry_run)
    else:
        saved_count = save_annual_data(symbol, data, dry_run)
    
    if not dry_run:
        log_result(symbol, table_name, 'success', saved_count, None)
    
    return True, saved_count


def main():
    parser = argparse.ArgumentParser(description='Update quarterly/annual financial results')
    parser.add_argument('--mode', choices=['quarterly', 'annual'], required=True,
                       help='What to update: quarterly or annual')
    parser.add_argument('--test', action='store_true',
                       help='Test mode: only process 3 symbols')
    parser.add_argument('--symbols', type=str,
                       help='Comma-separated list of symbols to process (e.g., RELIANCE,TCS)')
    parser.add_argument('--stale-days', type=int, default=90,
                       help='Consider data stale after N days (default: 90)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Preview what would be updated without saving')
    args = parser.parse_args()
    
    log_message("=" * 70)
    log_message(f"FINANCIAL RESULTS UPDATER - {args.mode.upper()}")
    log_message("=" * 70)
    log_message("")
    
    # Validate database
    try:
        validate_database()
    except Exception as e:
        log_message(f"[ERROR] Database validation failed: {e}")
        return False
    
    # Get symbols to update
    force_symbols = args.symbols.split(',') if args.symbols else None
    symbols = get_symbols_to_update(args.mode, args.stale_days, force_symbols)
    
    if args.test:
        symbols = symbols[:3]
        log_message(f"[TEST MODE] Processing {len(symbols)} symbols")
    
    if not symbols:
        log_message("No symbols to update!")
        return True
    
    if args.dry_run:
        log_message("[DRY-RUN MODE] No data will be saved")
    
    log_message(f"Processing {len(symbols)} symbols")
    log_message("")
    
    # Process symbols
    success_count = 0
    failed_count = 0
    start_time = time.time()
    
    for i, symbol in enumerate(symbols, 1):
        log_message(f"[{i}/{len(symbols)}] {symbol}...")
        
        success, result = process_symbol(symbol, args.mode, args.dry_run)
        
        if success:
            log_message(f"  ✓ Saved {result} {args.mode} periods")
            success_count += 1
        else:
            log_message(f"  ✗ Failed: {result}")
            failed_count += 1
        
        if not args.dry_run and i < len(symbols):
            time.sleep(RATE_LIMIT_DELAY)
    
    # Summary
    elapsed = (time.time() - start_time) / 60
    log_message("")
    log_message("=" * 70)
    log_message("SUMMARY")
    log_message("=" * 70)
    log_message(f"Total: {len(symbols)}")
    log_message(f"Success: {success_count}")
    log_message(f"Failed: {failed_count}")
    log_message(f"Time: {elapsed:.1f} minutes")
    log_message("")
    
    return success_count > 0


if __name__ == "__main__":
    try:
        success = main()
        exit(0 if success else 1)
    except KeyboardInterrupt:
        log_message("\n[INTERRUPTED] Stopped by user")
        exit(0)
    except Exception as e:
        log_message(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
