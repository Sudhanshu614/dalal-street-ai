"""
PHASE 2.4: SCRAPE CURRENT FUNDAMENTALS SNAPSHOT
Gets current fundamental metrics for all stocks from Screener.in

Data includes (18 fields):
- Market Cap, Current Price
- Valuation ratios (P/E, P/B, Dividend Yield)
- Performance metrics (ROE, ROCE, EPS)
- Shareholding (Promoter Holding)
- 52-week High/Low
- Debt-to-Equity, Book Value, Face Value

Usage:
    python scripts/scrape_fundamentals_database.py                # Run for all stocks
    python scripts/scrape_fundamentals_database.py --test         # Test mode (5 stocks)
    python scripts/scrape_fundamentals_database.py --resume       # Resume from last position
"""

import sqlite3
import requests
import re
import time
import sys
import argparse
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "data"))

from symbol_mappings import get_current_symbol

# Configuration
DB_FILE = Path(__file__).parent.parent / "database" / "stock_market.db"
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / f"scrape_fundamentals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# Constants
BASE_URL = "https://www.screener.in/company/{symbol}/consolidated/"

USER_AGENT = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

RATE_LIMIT_DELAY = 2
MAX_RETRIES = 3
RETRY_DELAY = 5

# Statistics
stats = {
    'success': 0,
    'not_found': 0,
    'errors': 0
}


def log_message(message, console=True, file=True):
    """Log to console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"

    if console:
        try:
            print(log_entry)
        except (UnicodeEncodeError, OSError):
            print(log_entry.encode('ascii', 'replace').decode('ascii'))

    if file:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_entry + '\n')


def parse_number(value_str):
    """Parse number from Screener format"""
    if not value_str or value_str.strip() in ['', '-', 'N/A', 'n.a.']:
        return None

    try:
        value_str = str(value_str).strip()

        # Remove percentage sign
        if '%' in value_str:
            value_str = value_str.replace('%', '').strip()

        # Remove commas and currency symbols
        value_str = re.sub(r'[₹,\s]', '', value_str)

        # Remove 'Cr' suffix
        value_str = value_str.replace('Cr', '').replace('cr', '').strip()

        # Remove trailing dots
        value_str = value_str.rstrip('.')

        return float(value_str)
    except (ValueError, AttributeError):
        return None


def setup_database():
    """Create fundamentals table"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fundamentals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            company_name TEXT,

            -- Market metrics
            market_cap REAL,
            current_price REAL,
            week52_high REAL,
            week52_low REAL,

            -- Valuation ratios
            pe_ratio REAL,
            pb_ratio REAL,
            book_value REAL,
            face_value REAL,
            dividend_yield REAL,

            -- Performance metrics
            eps REAL,
            roe REAL,
            roce REAL,

            -- Debt
            debt_to_equity REAL,

            -- Growth
            sales_growth REAL,
            profit_growth REAL,

            -- Shareholding
            promoter_holding REAL,

            last_updated TEXT,

            FOREIGN KEY (symbol) REFERENCES stocks_master(symbol)
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol ON fundamentals(symbol)')

    conn.commit()
    conn.close()

    log_message("[INFO] Database table 'fundamentals' ready")


def scrape_fundamentals(symbol):
    """Scrape fundamental data from Screener.in"""
    url = BASE_URL.format(symbol=symbol)

    try:
        response = requests.get(url, headers=USER_AGENT, timeout=15)

        if response.status_code == 404:
            return None, "not_found"

        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"

        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract company name
        company_name = None
        name_tag = soup.find('h1', class_='h2')
        if name_tag:
            company_name = name_tag.get_text(strip=True)

        # Extract top ratios
        fundamentals = {'symbol': symbol, 'company_name': company_name}

        top_ratios = soup.find('ul', {'id': 'top-ratios'})
        if not top_ratios:
            return None, "no_ratios"

        # Field mapping
        field_map = {
            'Market Cap': 'market_cap',
            'Current Price': 'current_price',
            'High / Low': 'week52_high',  # Will split this
            'Stock P/E': 'pe_ratio',
            'Book Value': 'book_value',
            'Dividend Yield': 'dividend_yield',
            'ROCE': 'roce',
            'ROE': 'roe',
            'Face Value': 'face_value',
            'Price to Book': 'pb_ratio',
            'Price to book': 'pb_ratio'
        }

        # Parse all ratio items
        for li in top_ratios.find_all('li'):
            name_span = li.find('span', class_='name')
            number_span = li.find('span', class_='number')

            if not name_span or not number_span:
                continue

            field_name = name_span.get_text(strip=True)
            value_str = number_span.get_text(strip=True)

            # Handle High/Low specially
            if field_name == 'High / Low':
                parts = value_str.split('/')
                if len(parts) == 2:
                    fundamentals['week52_high'] = parse_number(parts[0])
                    fundamentals['week52_low'] = parse_number(parts[1])
                continue

            # Map to database field
            if field_name in field_map:
                db_field = field_map[field_name]
                fundamentals[db_field] = parse_number(value_str)

        # Get additional metrics from quarterly results (latest EPS)
        quarters_section = soup.find('section', {'id': 'quarters'})
        if quarters_section:
            table = quarters_section.find('table', class_='data-table')
            if table:
                # Find EPS row
                rows = table.find('tbody').find_all('tr')
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 2:
                        metric = cells[0].get_text(strip=True)
                        if 'EPS' in metric.upper():
                            # Get latest quarter (first data column)
                            latest_eps = cells[1].get_text(strip=True)
                            if 'eps' not in fundamentals or fundamentals['eps'] is None:
                                fundamentals['eps'] = parse_number(latest_eps)
                            break

        # Get shareholding pattern (promoter holding)
        sh_section = soup.find('section', {'id': 'shareholding'})
        if sh_section:
            table = sh_section.find('table', class_='data-table')
            if table:
                rows = table.find('tbody').find_all('tr')
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 2:
                        metric = cells[0].get_text(strip=True)
                        if 'Promoter' in metric or 'Promoters' in metric:
                            # Get latest quarter
                            latest_value = cells[1].get_text(strip=True)
                            fundamentals['promoter_holding'] = parse_number(latest_value)
                            break

        # Get growth metrics from profit-loss section
        pl_section = soup.find('section', {'id': 'profit-loss'})
        if pl_section:
            table = pl_section.find('table', class_='data-table')
            if table:
                headers = table.find('thead').find_all('th')
                years = [th.get_text(strip=True) for th in headers[1:]]

                if len(years) >= 2:
                    rows = table.find('tbody').find_all('tr')
                    for row in rows:
                        cells = row.find_all('td')
                        if len(cells) >= 3:
                            metric = cells[0].get_text(strip=True)

                            # Sales growth
                            if metric == 'Sales' and len(cells) >= 3:
                                current = parse_number(cells[1].get_text(strip=True))
                                previous = parse_number(cells[2].get_text(strip=True))
                                if current and previous and previous != 0:
                                    fundamentals['sales_growth'] = ((current - previous) / previous) * 100

                            # Profit growth
                            if 'Net Profit' in metric and len(cells) >= 3:
                                current = parse_number(cells[1].get_text(strip=True))
                                previous = parse_number(cells[2].get_text(strip=True))
                                if current and previous and previous != 0:
                                    fundamentals['profit_growth'] = ((current - previous) / previous) * 100

        # Get debt-to-equity from ratios
        ratios_section = soup.find('section', {'id': 'ratios'})
        if ratios_section:
            table = ratios_section.find('table', class_='data-table')
            if table:
                rows = table.find('tbody').find_all('tr')
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 2:
                        metric = cells[0].get_text(strip=True)
                        if 'Debt' in metric and 'Equity' in metric:
                            latest_value = cells[1].get_text(strip=True)
                            fundamentals['debt_to_equity'] = parse_number(latest_value)
                            break

        return fundamentals, None

    except requests.Timeout:
        return None, "timeout"
    except requests.ConnectionError:
        return None, "connection_error"
    except Exception as e:
        return None, f"error: {str(e)}"


def save_fundamentals(data):
    """Save fundamental data to database"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('''
        INSERT OR REPLACE INTO fundamentals
        (symbol, company_name, market_cap, current_price, week52_high, week52_low,
         pe_ratio, pb_ratio, book_value, face_value, dividend_yield,
         eps, roe, roce, debt_to_equity,
         sales_growth, profit_growth, promoter_holding, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('symbol'),
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
        data.get('eps'),
        data.get('roe'),
        data.get('roce'),
        data.get('debt_to_equity'),
        data.get('sales_growth'),
        data.get('profit_growth'),
        data.get('promoter_holding'),
        last_updated
    ))

    conn.commit()
    conn.close()


def process_stock(symbol, index, total):
    """Process a single stock"""
    # Handle symbol mappings
    original_symbol = symbol
    screener_symbol = get_current_symbol(symbol)

    if screener_symbol != original_symbol:
        log_message(f"[{index}/{total}] {original_symbol} → {screener_symbol} (symbol changed)")
        symbol = screener_symbol

    # Scrape with retry
    for attempt in range(1, MAX_RETRIES + 1):
        data, error = scrape_fundamentals(symbol)

        if data is not None:
            break

        if error == "not_found":
            stats['not_found'] += 1
            log_message(f"[{index}/{total}] {symbol} - Not found (404)")
            return

        if attempt < MAX_RETRIES:
            log_message(f"[{index}/{total}] {symbol} - {error}, retry {attempt}/{MAX_RETRIES}")
            time.sleep(RETRY_DELAY)

    if data is None:
        stats['errors'] += 1
        log_message(f"[{index}/{total}] {symbol} - Error: {error}")
        return

    # Save to database
    save_fundamentals(data)

    stats['success'] += 1

    # Count non-null fields
    non_null = sum(1 for k, v in data.items() if v is not None and k not in ['symbol', 'company_name'])

    log_message(f"[{index}/{total}] {symbol} - Saved ({non_null} fields)")

    time.sleep(RATE_LIMIT_DELAY)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()

    log_message("="*70)
    log_message("PHASE 2.4: SCRAPE CURRENT FUNDAMENTALS")
    log_message("="*70)

    setup_database()

    # Get all stocks with company IDs
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol FROM company_ids WHERE status = 'found' AND company_id IS NOT NULL")
    all_stocks = [row[0] for row in cursor.fetchall()]
    conn.close()

    if args.test:
        all_stocks = all_stocks[:5]
        log_message(f"[INFO] TEST MODE: {len(all_stocks)} stocks")

    if args.resume:
        cursor = sqlite3.connect(DB_FILE).cursor()
        cursor.execute("SELECT symbol FROM fundamentals")
        done = set(row[0] for row in cursor.fetchall())
        all_stocks = [s for s in all_stocks if s not in done]
        log_message(f"[INFO] RESUME: {len(done)} done, {len(all_stocks)} remaining")

    total = len(all_stocks)
    log_message(f"[INFO] Total stocks: {total}")
    log_message(f"[INFO] Estimated time: {(total * RATE_LIMIT_DELAY) / 60:.1f} minutes\n")

    start_time = time.time()

    for index, symbol in enumerate(all_stocks, 1):
        process_stock(symbol, index, total)

        if index % 25 == 0:
            elapsed = (time.time() - start_time) / 60
            log_message(f"\nProgress: {index}/{total} | Success: {stats['success']} | Errors: {stats['errors']}")
            log_message(f"Elapsed: {elapsed:.1f}min\n")

    # Final summary
    log_message("\n" + "="*70)
    log_message("FINAL SUMMARY")
    log_message("="*70)
    log_message(f"Success: {stats['success']}")
    log_message(f"Not found: {stats['not_found']}")
    log_message(f"Errors: {stats['errors']}")
    log_message(f"Time: {(time.time() - start_time) / 60:.1f} minutes")
    log_message("="*70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_message("\n[INFO] Interrupted. Resume with --resume")
        sys.exit(0)
    except Exception as e:
        log_message(f"\n[ERROR] {str(e)}")
        import traceback
        log_message(traceback.format_exc())
        sys.exit(1)
