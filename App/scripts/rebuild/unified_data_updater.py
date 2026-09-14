"""
UNIFIED DATA UPDATER - Maximum Efficiency Scraper
Combines update_fundamentals.py + update_financial_results.py

Performance: 50% faster when updating all data (1 scrape instead of 2)

Flags:
  --basic      : Update basic fundamentals only (PE, ROE, prices, holdings)
  --enhanced   : Update enhanced fundamentals only (sector, growth rates, debt)
  --quarterly  : Update quarterly results only
  --annual     : Update annual financials only
  --all        : Update everything (DEFAULT - most efficient)
  
Resume & Testing:
  --start-from SYMBOL : Resume from specific symbol
  --limit N           : Process only N companies (testing)
  --dry-run           : Preview without saving
  --stale-days N      : Consider data stale after N days (default: 90)

Usage Examples:
  python unified_data_updater.py --all                    # Everything (recommended)
  python unified_data_updater.py --basic --quarterly      # Combination
  python unified_data_updater.py --enhanced --annual      # Another combo
  python unified_data_updater.py --all --limit 5          # Test on 5 companies
  python unified_data_updater.py --all --start-from RELIANCE  # Resume
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
USER_AGENT = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

RATE_LIMIT_DELAY = 2  # seconds
MAX_RETRIES = 3
RETRY_DELAY = 5


def log_message(message):
    """Log with timestamp"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] {message}")


def parse_number(text):
    """Parse Indian number format (e.g., '1,234.56 Cr.' → 1234.56)"""
    if not text or text.strip() in ['', '-', 'N/A', 'n.a.']:
        return None
    try:
        cleaned = text.replace('₹', '').replace(',', '').replace('%', '')
        cleaned = cleaned.replace('Cr', '').replace('cr', '').strip()
        match = re.search(r'[-+]?\d*\.?\d+', cleaned)
        if match:
            return float(match.group())
        return None
    except:
        return None


def parse_percentage(text):
    """Parse percentage (e.g., '12.5%' → 12.5)"""
    if not text:
        return None
    try:
        text = text.replace(',', '').replace('%', '').strip()
        match = re.search(r'[-+]?\d*\.?\d+', text)
        if match:
            return float(match.group())
        return None
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


def scrape_screener_all(symbol):
    """
    ONE request to Screener.in, extract ALL data
    
    Returns: (data_dict, error_string)
        data_dict = {
            'basic': {...},
            'enhanced': {...},
            'quarterly': {...},
            'annual': {...}
        }
    """
    url = BASE_URL.format(symbol=symbol)
    
    try:
        response = requests.get(url, headers=USER_AGENT, timeout=15)
        
        if response.status_code == 404:
            return None, "NOT_FOUND"
        
        if response.status_code != 200:
            return None, f"HTTP_{response.status_code}"
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Initialize data structure
        data = {
            'basic': {'symbol': symbol},
            'enhanced': {},
            'quarterly': {},
            'annual': {}
        }
        
        # ========================================
        # BASIC FUNDAMENTALS
        # ========================================
        
        # Company name
        company_name_tag = soup.find('h1', class_='h2')
        if company_name_tag:
            data['basic']['company_name'] = company_name_tag.get_text(strip=True)
        
        # Top ratios
        top_ratios = soup.find('ul', {'id': 'top-ratios'})
        if top_ratios:
            for li in top_ratios.find_all('li'):
                spans = li.find_all('span')
                if len(spans) >= 2:
                    field = spans[0].get_text(strip=True)
                    value = spans[1].get_text(strip=True)
                    
                    if field == 'Market Cap':
                        data['basic']['market_cap'] = parse_number(value)
                    elif field == 'Current Price':
                        data['basic']['current_price'] = parse_number(value)
                    elif field == 'High / Low':
                        parts = value.split('/')
                        if len(parts) == 2:
                            data['basic']['week52_high'] = parse_number(parts[0])
                            data['basic']['week52_low'] = parse_number(parts[1])
                    elif field == 'Stock P/E':
                        pe_v = parse_number(value)
                        data['basic']['pe_ratio'] = pe_v if (pe_v is None or pe_v > 0) else None
                    elif field == 'Book Value':
                        data['basic']['book_value'] = parse_number(value)
                    elif field == 'Dividend Yield':
                        data['basic']['dividend_yield'] = parse_number(value)
                    elif field == 'Face Value':
                        data['basic']['face_value'] = parse_number(value)
                    elif field == 'ROE':
                        data['basic']['roe'] = parse_number(value)
                    elif field == 'ROCE':
                        data['basic']['roce'] = parse_number(value)
                    elif field == 'EPS':
                        data['basic']['eps'] = parse_number(value)
        
        # Calculate PB ratio
        if data['basic'].get('current_price') and data['basic'].get('book_value'):
            if data['basic']['book_value'] != 0 and data['basic']['current_price'] > 0:
                data['basic']['pb_ratio'] = data['basic']['current_price'] / data['basic']['book_value']
        
        # Shareholding
        sh_section = soup.find('section', {'id': 'shareholding'})
        if sh_section:
            rows = sh_section.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True)
                    value = cells[1].get_text(strip=True)
                    
                    if 'Promoter' in label:
                        data['basic']['promoter_holding'] = parse_number(value)
                    elif 'FII' in label:
                        data['basic']['fii_holding'] = parse_number(value)
                    elif 'DII' in label:
                        data['basic']['dii_holding'] = parse_number(value)
        
        # ========================================
        # ENHANCED FUNDAMENTALS
        # ========================================
        
        # Industry/Sector from peers section
        peer_section = soup.find('section', {'id': 'peers'})
        if peer_section:
            all_links = peer_section.find_all('a', href=True)
            categories = []
            
            for link in all_links:
                href = link.get('href', '')
                if '/market/IN' in href and len(categories) < 4:
                    category = link.get_text(strip=True)
                    if category and category not in categories:
                        categories.append(category)
            
            if len(categories) >= 1:
                data['enhanced']['sector'] = categories[0]
            if len(categories) >= 2:
                data['enhanced']['industry'] = categories[1]
            if len(categories) >= 3:
                data['enhanced']['subsector'] = categories[2]
            if len(categories) >= 4:
                data['enhanced']['business_segment'] = categories[3]
        
        # Growth metrics
        for table in soup.find_all('table'):
            headers = table.find_all('th')
            if not headers:
                continue
            
            header_text = headers[0].get_text(strip=True)
            
            # Sales Growth
            if 'Compounded Sales Growth' in header_text:
                rows = table.find_all('tr')
                for row in rows[1:]:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        period = cells[0].get_text(strip=True)
                        value = cells[1].get_text(strip=True)
                        
                        if '10 Years' in period or '10 Year' in period:
                            data['enhanced']['sales_growth_10year'] = parse_percentage(value)
                        elif '5 Years' in period or '5 Year' in period:
                            data['enhanced']['sales_growth_5year'] = parse_percentage(value)
                        elif '3 Years' in period or '3 Year' in period:
                            data['enhanced']['sales_growth_3year'] = parse_percentage(value)
            
            # Profit Growth
            elif 'Compounded Profit Growth' in header_text:
                rows = table.find_all('tr')
                for row in rows[1:]:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        period = cells[0].get_text(strip=True)
                        value = cells[1].get_text(strip=True)
                        
                        if '10 Years' in period or '10 Year' in period:
                            data['enhanced']['profit_growth_10year'] = parse_percentage(value)
                        elif '5 Years' in period or '5 Year' in period:
                            data['enhanced']['profit_growth_5year'] = parse_percentage(value)
                        elif '3 Years' in period or '3 Year' in period:
                            data['enhanced']['profit_growth_3year'] = parse_percentage(value)
        
        # Debt metrics
        balance_sheet_section = soup.find('section', {'id': 'quarters'}) or soup.find('section', {'id': 'balance-sheet'})
        if balance_sheet_section:
            for table in balance_sheet_section.find_all('table'):
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    if not cells:
                        continue
                    
                    row_label = cells[0].get_text(strip=True).lower()
                    
                    if 'debt to equity' in row_label or 'debt equity' in row_label:
                        if len(cells) >= 2:
                            value = cells[1].get_text(strip=True)
                            data['enhanced']['debt_to_equity'] = parse_number(value)
                    
                    elif 'deposits' in row_label and 'total' in row_label:
                        if len(cells) >= 2:
                            value = cells[1].get_text(strip=True)
                            data['enhanced']['total_deposits'] = parse_number(value)
        
        # ========================================
        # QUARTERLY RESULTS
        # ========================================
        
        section = soup.find('section', {'id': 'quarters'})
        if section:
            table = section.find('table', class_='data-table')
            if table:
                headers = table.find('thead').find_all('th')
                quarters = [th.get_text(strip=True) for th in headers[1:]]
                
                if quarters:
                    quarterly_data = {q: {} for q in quarters}
                    tbody = table.find('tbody')
                    
                    if tbody:
                        rows = tbody.find_all('tr')
                        for row in rows:
                            cells = row.find_all('td')
                            if len(cells) < 2:
                                continue
                            
                            metric_name = cells[0].get_text(strip=True).replace('+', '').strip()
                            
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
                                        value = parse_number(cell.get_text(strip=True))
                                        quarterly_data[quarter][field] = value
                    
                    data['quarterly'] = quarterly_data
        
        # ========================================
        # ANNUAL FINANCIALS
        # ========================================
        
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
                                value = parse_number(cell.get_text(strip=True))
                                annual_data[year][field] = value
            
            return annual_data
        
        # Profit & Loss
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
        
        # Balance Sheet
        bs_mapping = {
            'Equity Capital': 'equity_capital',
            'Reserves': 'reserves',
            'Borrowings': 'borrowings',
            'Fixed Assets': 'fixed_assets',
            'Total Assets': 'total_assets'
        }
        bs_data = scrape_table('balance-sheet', bs_mapping)
        
        # Merge all annual data
        all_years = set()
        for d in [pl_data, bs_data]:
            all_years.update(d.keys())
        
        # Filter out TTM
        filtered_years = {year for year in all_years if 'TTM' not in year.upper()}
        
        merged_annual = {}
        for year in filtered_years:
            merged_annual[year] = {}
            for d in [pl_data, bs_data]:
                if year in d:
                    merged_annual[year].update(d[year])
        
        data['annual'] = merged_annual
        
        return data, None
        
    except requests.Timeout:
        return None, "TIMEOUT"
    except Exception as e:
        return None, f"ERROR: {str(e)}"


def get_symbols_to_update(modes, stale_days=90, start_from=None):
    """
    Get symbols needing updates based on requested modes
    
    modes: list of ['basic', 'enhanced', 'quarterly', 'annual']
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    all_symbols = set()
    
    # Basic or Enhanced fundamentals
    if 'basic' in modes or 'enhanced' in modes:
        # Get symbols from fundamentals that are stale or new
        stale_date = (datetime.now() - timedelta(days=stale_days)).strftime('%Y-%m-%d')
        cursor.execute(f"""
            SELECT symbol FROM fundamentals 
            WHERE (last_updated IS NULL OR last_updated < ?)
            AND symbol IN (SELECT symbol FROM stocks_master WHERE is_active = 1)
        """, (stale_date,))
        all_symbols.update(row[0] for row in cursor.fetchall())
    
    # Quarterly results
    if 'quarterly' in modes:
        cursor.execute("""
            SELECT symbol FROM fundamentals 
            WHERE symbol NOT IN (SELECT DISTINCT symbol FROM quarterly_results)
            AND symbol IN (SELECT symbol FROM stocks_master WHERE is_active = 1)
        """)
        all_symbols.update(row[0] for row in cursor.fetchall())
        
        # Also get stale quarterly data
        stale_date = (datetime.now() - timedelta(days=stale_days)).strftime('%Y-%m-%d')
        cursor.execute(f"""
            SELECT DISTINCT symbol FROM quarterly_results
            WHERE last_updated < ?
            AND symbol IN (SELECT symbol FROM stocks_master WHERE is_active = 1)
        """, (stale_date,))
        all_symbols.update(row[0] for row in cursor.fetchall())
    
    # Annual financials
    if 'annual' in modes:
        cursor.execute("""
            SELECT symbol FROM fundamentals 
            WHERE symbol NOT IN (SELECT DISTINCT symbol FROM annual_financials)
            AND symbol IN (SELECT symbol FROM stocks_master WHERE is_active = 1)
        """)
        all_symbols.update(row[0] for row in cursor.fetchall())
        
        # Also get stale annual data
        stale_date = (datetime.now() - timedelta(days=stale_days)).strftime('%Y-%m-%d')
        cursor.execute(f"""
            SELECT DISTINCT symbol FROM annual_financials
            WHERE last_updated < ?
            AND symbol IN (SELECT symbol FROM stocks_master WHERE is_active = 1)
        """, (stale_date,))
        all_symbols.update(row[0] for row in cursor.fetchall())
    
    conn.close()
    
    symbols = sorted(list(all_symbols))
    
    # Handle start_from resumption
    if start_from and start_from in symbols:
        start_index = symbols.index(start_from)
        symbols = symbols[start_index:]
        log_message(f"Resuming from {start_from} (skipping {start_index} symbols)")
    
    return symbols


def save_data(symbol, data, modes, dry_run=False):
    """
    Save data to database based on requested modes
    Returns: dict of {mode: count}
    """
    if dry_run:
        counts = {}
        if 'basic' in modes:
            counts['basic'] = 1
        if 'enhanced' in modes:
            counts['enhanced'] = 1
        if 'quarterly' in modes:
            counts['quarterly'] = len(data['quarterly'])
        if 'annual' in modes:
            counts['annual'] = len(data['annual'])
        return counts
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    counts = {}
    
    # Save basic fundamentals
    if 'basic' in modes:
        basic = data['basic']
        cursor.execute('''
            INSERT INTO fundamentals
            (symbol, company_name, market_cap, current_price, week52_high, week52_low,
             pe_ratio, pb_ratio, book_value, face_value, dividend_yield,
             roe, roce, eps, promoter_holding, fii_holding, dii_holding,
             data_source, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'screener.in', ?)
            ON CONFLICT(symbol) DO UPDATE SET
                company_name = excluded.company_name,
                market_cap = excluded.market_cap,
                current_price = excluded.current_price,
                week52_high = excluded.week52_high,
                week52_low = excluded.week52_low,
                pe_ratio = excluded.pe_ratio,
                pb_ratio = excluded.pb_ratio,
                book_value = excluded.book_value,
                face_value = excluded.face_value,
                dividend_yield = excluded.dividend_yield,
                roe = excluded.roe,
                roce = excluded.roce,
                eps = excluded.eps,
                promoter_holding = excluded.promoter_holding,
                fii_holding = excluded.fii_holding,
                dii_holding = excluded.dii_holding,
                data_source = 'screener.in',
                last_updated = excluded.last_updated
        ''', (
            symbol,
            basic.get('company_name'),
            basic.get('market_cap'),
            basic.get('current_price'),
            basic.get('week52_high'),
            basic.get('week52_low'),
            basic.get('pe_ratio'),
            basic.get('pb_ratio'),
            basic.get('book_value'),
            basic.get('face_value'),
            basic.get('dividend_yield'),
            basic.get('roe'),
            basic.get('roce'),
            basic.get('eps'),
            basic.get('promoter_holding'),
            basic.get('fii_holding'),
            basic.get('dii_holding'),
            last_updated
        ))
        counts['basic'] = 1
    
    # Save enhanced fundamentals
    if 'enhanced' in modes:
        enhanced = data['enhanced']
        cursor.execute('''
            UPDATE fundamentals SET 
                industry = COALESCE(?, industry),
                sector = COALESCE(?, sector),
                subsector = COALESCE(?, subsector),
                business_segment = COALESCE(?, business_segment),
                sales_growth_3year = COALESCE(?, sales_growth_3year),
                sales_growth_5year = COALESCE(?, sales_growth_5year),
                sales_growth_10year = COALESCE(?, sales_growth_10year),
                profit_growth_3year = COALESCE(?, profit_growth_3year),
                profit_growth_5year = COALESCE(?, profit_growth_5year),
                profit_growth_10year = COALESCE(?, profit_growth_10year),
                debt_to_equity = COALESCE(?, debt_to_equity),
                total_deposits = COALESCE(?, total_deposits),
                last_updated = ?
            WHERE symbol = ?
        ''', (
            enhanced.get('industry'),
            enhanced.get('sector'),
            enhanced.get('subsector'),
            enhanced.get('business_segment'),
            enhanced.get('sales_growth_3year'),
            enhanced.get('sales_growth_5year'),
            enhanced.get('sales_growth_10year'),
            enhanced.get('profit_growth_3year'),
            enhanced.get('profit_growth_5year'),
            enhanced.get('profit_growth_10year'),
            enhanced.get('debt_to_equity'),
            enhanced.get('total_deposits'),
            last_updated,
            symbol
        ))
        counts['enhanced'] = 1
    
    # Save quarterly results
    if 'quarterly' in modes:
        quarterly_count = 0
        for quarter, values in data['quarterly'].items():
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
            quarterly_count += 1
        counts['quarterly'] = quarterly_count
    
    # Save annual financials
    if 'annual' in modes:
        annual_count = 0
        for year, values in data['annual'].items():
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
            annual_count += 1
        counts['annual'] = annual_count
    
    conn.commit()
    conn.close()
    
    return counts


def process_symbol(symbol, modes, dry_run=False):
    """Process a single symbol"""
    # Scrape with retry
    for attempt in range(1, MAX_RETRIES + 1):
        data, error = scrape_screener_all(symbol)
        
        if data is not None:
            break
        
        if attempt < MAX_RETRIES:
            log_message(f"  Retry {attempt}/{MAX_RETRIES} after {error}")
            time.sleep(RETRY_DELAY)
    
    if data is None:
        return False, error
    
    # Save data
    counts = save_data(symbol, data, modes, dry_run)
    
    return True, counts


def main():
    parser = argparse.ArgumentParser(description='Unified Data Updater - Maximum Efficiency')
    parser.add_argument('--basic', action='store_true',
                       help='Update basic fundamentals only')
    parser.add_argument('--enhanced', action='store_true',
                       help='Update enhanced fundamentals only')
    parser.add_argument('--quarterly', action='store_true',
                       help='Update quarterly results only')
    parser.add_argument('--annual', action='store_true',
                       help='Update annual financials only')
    parser.add_argument('--all', action='store_true',
                       help='Update everything (DEFAULT if no flags)')
    parser.add_argument('--start-from', type=str,
                       help='Resume from specific symbol')
    parser.add_argument('--limit', type=int,
                       help='Process only N companies (testing)')
    parser.add_argument('--stale-days', type=int, default=90,
                       help='Consider data stale after N days (default: 90)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Preview without saving')
    args = parser.parse_args()
    
    # Determine modes
    modes = []
    if args.basic:
        modes.append('basic')
    if args.enhanced:
        modes.append('enhanced')
    if args.quarterly:
        modes.append('quarterly')
    if args.annual:
        modes.append('annual')
    
    # Default to --all if no flags
    if not modes or args.all:
        modes = ['basic', 'enhanced', 'quarterly', 'annual']
    
    mode_str = ' + '.join(modes).upper()
    
    log_message("=" * 70)
    log_message(f"UNIFIED DATA UPDATER - {mode_str}")
    log_message("=" * 70)
    log_message("")
    
    # Get symbols to update
    symbols = get_symbols_to_update(modes, args.stale_days, args.start_from)
    
    if args.limit:
        symbols = symbols[:args.limit]
        log_message(f"[LIMIT] Processing {len(symbols)} symbols")
    
    if not symbols:
        log_message("No symbols to update!")
        return True
    
    if args.dry_run:
        log_message("[DRY-RUN MODE] No data will be saved")
    
    log_message(f"Processing {len(symbols)} symbols")
    log_message(f"Modes: {', '.join(modes)}")
    log_message("")
    
    # Process symbols
    success_count = 0
    failed_count = 0
    start_time = time.time()
    
    for i, symbol in enumerate(symbols, 1):
        log_message(f"[{i}/{len(symbols)}] {symbol}...")
        
        success, result = process_symbol(symbol, modes, args.dry_run)
        
        if success:
            if args.dry_run:
                log_message(f"  ✓ Would update: {result}")
            else:
                log_message(f"  ✓ Updated: {result}")
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
