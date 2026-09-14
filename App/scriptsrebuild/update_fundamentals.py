"""
UNIFIED FUNDAMENTALS SCRAPER
Merges 06_rescrape_failed_stocks.py and 08_scrape_enhanced_fundamentals.py

Purpose: ONE Screener.in scrape per company gets ALL fundamental data
- Basic fundamentals: market_cap, prices, ratios, holdings
- Enhanced fundamentals: industry, sector, growth rates, debt

Flags:
  --basic: Scrape ONLY basic fundamentals (fast)
  --enhanced: Scrape ONLY enhanced fundamentals
  (no flags): DEFAULT - scrape BOTH basic + enhanced (recommended)

Usage:
  python update_fundamentals.py                    # Full update (default)
  python update_fundamentals.py --basic            # Basic only
  python update_fundamentals.py --enhanced         # Enhanced only
  python update_fundamentals.py --limit 10         # Test on 10 companies
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import time
import requests
from bs4 import BeautifulSoup
import argparse
import sys
import re

# Database path (relative to script location)
SCRIPT_DIR = Path(__file__).resolve().parent
DB_FILE = SCRIPT_DIR.parent / 'database' / 'stock_market_new.db'


def log_message(message):
    """Log to console with timestamp."""
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] {message}")


def parse_number(text):
    """Parse Indian number format (e.g., '1,234.56 Cr.' → 1234560000)"""
    if not text:
        return None

    try:
        # Remove commas and convert to lowercase
        text = text.replace(',', '').lower()

        # Extract number
        match = re.search(r'[-+]?\d*\.?\d+', text)
        if not match:
            return None

        num = float(match.group())

        # Handle units
        if 'cr' in text or 'crore' in text:
            num *= 10000000  # 1 crore = 10 million
        elif 'lac' in text or 'lakh' in text:
            num *= 100000

        return num
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


def scrape_screener_full(symbol):
    """
    Scrape ALL fundamental data from Screener.in in ONE request.
    
    Returns: (data_dict, error_string)
        data_dict contains 'basic' and 'enhanced' keys
        error_string is None if successful
    """
    url = f"https://www.screener.in/company/{symbol}/consolidated/"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    try:
        response = requests.get(url, headers=headers, timeout=15)

        if response.status_code == 404:
            return None, "NOT_FOUND"

        if response.status_code != 200:
            return None, f"HTTP_{response.status_code}"

        soup = BeautifulSoup(response.text, 'html.parser')

        # ============================================================
        # BASIC FUNDAMENTALS (from top ratios section)
        # ============================================================
        basic = {'symbol': symbol}
        
        # Company name
        company_name_tag = soup.find('h1', class_='h2')
        if company_name_tag:
            basic['company_name'] = company_name_tag.get_text(strip=True)

        # Top ratios
        top_ratios = soup.find('ul', {'id': 'top-ratios'})
        if top_ratios:
            for li in top_ratios.find_all('li'):
                spans = li.find_all('span')
                if len(spans) >= 2:
                    field = spans[0].get_text(strip=True)
                    value = spans[1].get_text(strip=True)

                    if field == 'Market Cap':
                        basic['market_cap'] = parse_number(value)
                    elif field == 'Current Price':
                        basic['current_price'] = parse_number(value)
                    elif field == 'High / Low':
                        parts = value.split('/')
                        if len(parts) == 2:
                            basic['week52_high'] = parse_number(parts[0])
                            basic['week52_low'] = parse_number(parts[1])
                    elif field == 'Stock P/E':
                        pe_v = parse_number(value)
                        basic['pe_ratio'] = pe_v if (pe_v is None or pe_v > 0) else None
                    elif field == 'Book Value':
                        basic['book_value'] = parse_number(value)  # Can be negative
                    elif field == 'Dividend Yield':
                        basic['dividend_yield'] = parse_number(value)
                    elif field == 'Face Value':
                        basic['face_value'] = parse_number(value)
                    elif field == 'ROE':
                        basic['roe'] = parse_number(value)
                    elif field == 'ROCE':
                        basic['roce'] = parse_number(value)
                    elif field == 'EPS':
                        basic['eps'] = parse_number(value)

        # Calculate PB ratio if possible
        if basic.get('current_price') and basic.get('book_value') and basic['book_value'] != 0:
            if basic['current_price'] > 0:
                basic['pb_ratio'] = basic['current_price'] / basic['book_value']

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
                        basic['promoter_holding'] = parse_number(value)
                    elif 'FII' in label:
                        basic['fii_holding'] = parse_number(value)
                    elif 'DII' in label:
                        basic['dii_holding'] = parse_number(value)

        # ============================================================
        # ENHANCED FUNDAMENTALS
        # ============================================================
        enhanced = {}

        # A. Industry/Sector Classification (from peers section)
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

            # Assign hierarchy: Sector > Industry > Subsector > Segment
            if len(categories) >= 1:
                enhanced['sector'] = categories[0]
            if len(categories) >= 2:
                enhanced['industry'] = categories[1]
            if len(categories) >= 3:
                enhanced['subsector'] = categories[2]
            if len(categories) >= 4:
                enhanced['business_segment'] = categories[3]

        # B. Growth Metrics (from compounded growth tables)
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
                            enhanced['sales_growth_10year'] = parse_percentage(value)
                        elif '5 Years' in period or '5 Year' in period:
                            enhanced['sales_growth_5year'] = parse_percentage(value)
                        elif '3 Years' in period or '3 Year' in period:
                            enhanced['sales_growth_3year'] = parse_percentage(value)

            # Profit Growth
            elif 'Compounded Profit Growth' in header_text:
                rows = table.find_all('tr')
                for row in rows[1:]:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        period = cells[0].get_text(strip=True)
                        value = cells[1].get_text(strip=True)

                        if '10 Years' in period or '10 Year' in period:
                            enhanced['profit_growth_10year'] = parse_percentage(value)
                        elif '5 Years' in period or '5 Year' in period:
                            enhanced['profit_growth_5year'] = parse_percentage(value)
                        elif '3 Years' in period or '3 Year' in period:
                            enhanced['profit_growth_3year'] = parse_percentage(value)

        # C. Balance Sheet (debt metrics)
        balance_sheet_section = soup.find('section', {'id': 'quarters'}) or soup.find('section', {'id': 'balance-sheet'})
        if balance_sheet_section:
            for table in balance_sheet_section.find_all('table'):
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    if not cells:
                        continue
                    
                    row_label = cells[0].get_text(strip=True).lower()
                    
                    # Debt to Equity
                    if 'debt to equity' in row_label or 'debt equity' in row_label:
                        if len(cells) >= 2:
                            value = cells[1].get_text(strip=True)
                            enhanced['debt_to_equity'] = parse_number(value)
                    
                    # Total Deposits (for banks)
                    elif 'deposits' in row_label and 'total' in row_label:
                        if len(cells) >= 2:
                            value = cells[1].get_text(strip=True)
                            enhanced['total_deposits'] = parse_number(value)

        return {'basic': basic, 'enhanced': enhanced}, None

    except Exception as e:
        return None, str(e)


def update_basic_fundamentals(conn, symbol, data):
    """Update basic fundamentals fields."""
    cursor = conn.cursor()
    
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
        data.get('company_name'),
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
        data.get('eps'),
        data.get('promoter_holding'),
        data.get('fii_holding'),
        data.get('dii_holding'),
        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ))


def update_enhanced_fundamentals(conn, symbol, data):
    """Update enhanced fundamentals fields."""
    cursor = conn.cursor()
    
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
        data.get('industry'),
        data.get('sector'),
        data.get('subsector'),
        data.get('business_segment'),
        data.get('sales_growth_3year'),
        data.get('sales_growth_5year'),
        data.get('sales_growth_10year'),
        data.get('profit_growth_3year'),
        data.get('profit_growth_5year'),
        data.get('profit_growth_10year'),
        data.get('debt_to_equity'),
        data.get('total_deposits'),
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        symbol
    ))


def get_symbols_to_update(conn, limit=0, start_from=None):
    """Get list of symbols to update from fundamentals table."""
    cursor = conn.cursor()
    
    if limit and limit > 0:
        cursor.execute("SELECT symbol FROM fundamentals ORDER BY symbol LIMIT ?", (limit,))
    else:
        cursor.execute("SELECT symbol FROM fundamentals ORDER BY symbol")
    
    symbols = [row[0] for row in cursor.fetchall()]
    
    # If start_from is specified, skip all symbols until we reach it
    if start_from:
        try:
            start_index = symbols.index(start_from)
            symbols = symbols[start_index:]
            log_message(f"Resuming from {start_from} (skipping {start_index} companies)")
        except ValueError:
            log_message(f"Warning: {start_from} not found in list, starting from beginning")
    
    return symbols


def main():
    parser = argparse.ArgumentParser(
        description='Update fundamentals by scraping Screener.in'
    )
    parser.add_argument('--basic', action='store_true',
                       help='Scrape ONLY basic fundamentals (market_cap, price, ratios, holdings)')
    parser.add_argument('--enhanced', action='store_true',
                       help='Scrape ONLY enhanced fundamentals (industry, growth rates, debt)')
    parser.add_argument('--limit', type=int, default=0,
                       help='Limit number of companies to process')
    parser.add_argument('--start-from', type=str, default=None,
                       help='Resume from a specific symbol (e.g., RELAXO)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Preview only, do not update database')
    args = parser.parse_args()

    # Determine what to scrape
    if not args.basic and not args.enhanced:
        # Default: scrape both
        scrape_basic = True
        scrape_enhanced = True
        mode = "FULL (basic + enhanced)"
    elif args.basic and not args.enhanced:
        scrape_basic = True
        scrape_enhanced = False
        mode = "BASIC only"
    elif args.enhanced and not args.basic:
        scrape_basic = False
        scrape_enhanced = True
        mode = "ENHANCED only"
    else:
        # Both flags set
        scrape_basic = True
        scrape_enhanced = True
        mode = "FULL (both flags set)"

    log_message("="*70)
    log_message(f"UNIFIED FUNDAMENTALS SCRAPER - {mode}")
    log_message("="*70)
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found")
        return False

    conn = sqlite3.connect(DB_FILE)
    
    # Get symbols to process
    symbols = get_symbols_to_update(conn, limit=args.limit, start_from=args.start_from)
    total = len(symbols)
    
    log_message(f"Processing {total} companies")
    log_message(f"Scraping: {'Basic' if scrape_basic else ''}{' + ' if scrape_basic and scrape_enhanced else ''}{'Enhanced' if scrape_enhanced else ''}")
    log_message("")

    success_count = 0
    failed_count = 0
    
    for i, symbol in enumerate(symbols, 1):
        log_message(f"[{i}/{total}] {symbol}...")

        # Scrape full data (one request)
        full_data, error = scrape_screener_full(symbol)

        if error:
            log_message(f"  [FAIL] {error}")
            failed_count += 1
            time.sleep(2)
            continue

        if not full_data:
            log_message(f"  [FAIL] No data returned")
            failed_count += 1
            time.sleep(2)
            continue

        try:
            if not args.dry_run:
                # Update based on flags
                if scrape_basic:
                    update_basic_fundamentals(conn, symbol, full_data['basic'])
                
                if scrape_enhanced:
                    update_enhanced_fundamentals(conn, symbol, full_data['enhanced'])
                
                conn.commit()

            # Show what was captured
            captured = []
            if scrape_basic and full_data['basic'].get('market_cap'):
                captured.append('Basic')
            if scrape_enhanced and full_data['enhanced'].get('industry'):
                captured.append('Enhanced')
            
            log_message(f"  [OK] Updated: {', '.join(captured) if captured else 'minimal'}")
            success_count += 1

        except Exception as e:
            log_message(f"  [FAIL] Database error: {e}")
            failed_count += 1

        time.sleep(2)  # Rate limiting

    conn.close()

    log_message("")
    log_message("="*70)
    log_message("SUMMARY")
    log_message("="*70)
    log_message(f"Total: {total}")
    log_message(f"Success: {success_count}")
    log_message(f"Failed: {failed_count}")
    log_message("")

    if success_count > 0:
        log_message("[SUCCESS] Fundamentals update complete!")
        log_message("")
        if scrape_basic:
            log_message("Basic fundamentals updated:")
            log_message("  - Market Cap, Current Price, 52W High/Low")
            log_message("  - PE, PB, Book Value, ROE, ROCE, EPS")
            log_message("  - Promoter/FII/DII Holdings")
        if scrape_enhanced:
            log_message("")
            log_message("Enhanced fundamentals updated:")
            log_message("  - Industry, Sector classification")
            log_message("  - Sales/Profit Growth (3Y, 5Y, 10Y)")
            log_message("  - Debt to Equity, Total Deposits")
        log_message("")

    return success_count > 0


if __name__ == "__main__":
    try:
        success = main()
        exit(0 if success else 1)
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
