"""
PHASE 6: RE-SCRAPE FAILED STOCKS (NEGATIVE BOOK VALUE)
Re-scrapes the 80 stocks that failed due to negative book value

Background:
- 80 stocks failed with "CHECK constraint failed: book_value"
- After migration, book_value constraint removed
- Now we can capture partial fundamentals (market_cap, price, PE, holdings) even with negative book_value

These stocks are mostly bankrupt/distressed companies:
- ABAN, IDEA, RCOM, UNITECH, MTNL (telecom crisis)
- GTL, GTLINFRA (infrastructure defaults)
- IL&FSENGG (NBFC crisis)
- Real estate (PARSVNATH, OMAXE, UNITECH)

Usage:
    python scripts/rebuild/06_rescrape_failed_stocks.py
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import time
import requests
from bs4 import BeautifulSoup
import argparse
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))
from App.config import config
DB_FILE = Path(config.DB_PATH)

def _select_symbols(conn, all_mode=False, only_missing=False, limit=0, symbols_file=None):
    cur = conn.cursor()
    syms = []
    if symbols_file:
        try:
            txt = Path(symbols_file).read_text(encoding='utf-8')
            syms = [s.strip().upper() for s in txt.splitlines() if s.strip()]
        except Exception:
            syms = []
    elif all_mode:
        cur.execute("SELECT symbol FROM stocks_master WHERE is_active = 1")
        syms = [r[0] for r in cur.fetchall()]
    elif only_missing:
        cur.execute("SELECT symbol FROM fundamentals WHERE current_price IS NULL OR week52_high IS NULL OR week52_low IS NULL OR pe_ratio IS NULL")
        syms = [r[0] for r in cur.fetchall()]
    else:
        cur.execute("SELECT symbol FROM fundamentals")
        syms = [r[0] for r in cur.fetchall()]
    if limit and limit > 0:
        syms = syms[:limit]
    return syms

def log_message(message):
    print(message)

def parse_number(text):
    """Parse Indian number format (e.g., '1,234.56 Cr.' → 1234560000)"""
    if not text:
        return None

    try:
        # Remove commas and convert to lowercase
        text = text.replace(',', '').lower()

        # Extract number
        import re
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

def scrape_screener(symbol):
    """Scrape fundamental data from Screener.in (allows negative book_value!)"""
    url = f"https://www.screener.in/company/{symbol}/consolidated/"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 404:
            return None, "NOT_FOUND"

        if response.status_code != 200:
            return None, f"HTTP_{response.status_code}"

        soup = BeautifulSoup(response.text, 'html.parser')

        
        company_name_tag = soup.find('h1', class_='h2')
        company_name = company_name_tag.get_text(strip=True) if company_name_tag else None

        data = {
            'symbol': symbol,
            'company_name': company_name
        }

        top_ratios = soup.find('ul', {'id': 'top-ratios'})
        if top_ratios:
            for li in top_ratios.find_all('li'):
                spans = li.find_all('span')
                if len(spans) >= 2:
                    name = spans[0]
                    number = spans[1]

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
                        pe_v = parse_number(value)
                        data['pe_ratio'] = pe_v if (pe_v is None or pe_v > 0) else None
                    elif field == 'Book Value':
                        data['book_value'] = parse_number(value)  # CAN BE NEGATIVE!
                    elif field == 'Dividend Yield':
                        data['dividend_yield'] = parse_number(value)
                    elif field == 'Face Value':
                        data['face_value'] = parse_number(value)
                    elif field == 'ROE':
                        data['roe'] = parse_number(value)
                    elif field == 'ROCE':
                        data['roce'] = parse_number(value)
                    elif field == 'EPS':
                        data['eps'] = parse_number(value)

        if data.get('current_price') and data.get('book_value') and data['book_value'] != 0:
            if data['current_price'] > 0:
                data['pb_ratio'] = data['current_price'] / data['book_value']

        # Parse shareholding
        sh_section = soup.find('section', {'id': 'shareholding'})
        if sh_section:
            rows = sh_section.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True)
                    value = cells[1].get_text(strip=True)

                    if 'Promoter' in label:
                        data['promoter_holding'] = parse_number(value)
                    elif 'FII' in label:
                        data['fii_holding'] = parse_number(value)
                    elif 'DII' in label:
                        data['dii_holding'] = parse_number(value)

        return data, None

    except Exception as e:
        return None, str(e)

def rescrape_failed_stocks(all_mode=False, only_missing=False, limit=0, symbols_file=None, dry_run=False):
    log_message("="*70)
    log_message("RE-SCRAPE FAILED STOCKS (NEGATIVE BOOK VALUE)")
    log_message("="*70)
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found")
        return False

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    targets = _select_symbols(conn, all_mode=all_mode, only_missing=only_missing, limit=limit, symbols_file=symbols_file)
    total = len(targets)
    success_count = 0
    failed_count = 0
    still_negative_bv = 0
    log_message(f"[INFO] Re-scraping {total} stocks")
    log_message("")
    for i, symbol in enumerate(targets, 1):
        log_message(f"[{i}/{total}] {symbol}...")

        data, error = scrape_screener(symbol)

        if error:
            log_message(f"  [FAIL] {error}")
            failed_count += 1
            time.sleep(2)
            continue

        if not data:
            log_message(f"  [FAIL] No data returned")
            failed_count += 1
            time.sleep(2)
            continue

        book_value = data.get('book_value')
        if book_value is not None and book_value < 0:
            still_negative_bv += 1
            log_message(f"  [INFO] Book Value: {book_value:.2f} (NEGATIVE - but OK now!)")

        try:
            if not dry_run:
                cursor.execute('''
                    INSERT OR REPLACE INTO fundamentals
                    (symbol, company_name, market_cap, current_price, week52_high, week52_low,
                     pe_ratio, pb_ratio, book_value, face_value, dividend_yield,
                     roe, roce, eps, promoter_holding, fii_holding, dii_holding,
                     data_source, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'screener.in', ?)
                ''', (
                    data.get('symbol'),
                    data.get('company_name'),
                    data.get('market_cap'),
                    (data.get('current_price') if (data.get('current_price') is None or data.get('current_price') > 0) else None),
                    data.get('week52_high'),
                    data.get('week52_low'),
                    (data.get('pe_ratio') if (data.get('pe_ratio') is None or data.get('pe_ratio') > 0) else None),
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
            if not dry_run:
                conn.commit()

            # Show what data we captured
            captured_fields = []
            if data.get('market_cap'):
                captured_fields.append('Market Cap')
            if data.get('current_price'):
                captured_fields.append('Price')
            if data.get('pe_ratio'):
                captured_fields.append('PE')
            if data.get('promoter_holding'):
                captured_fields.append('Promoter%')

            log_message(f"  [OK] Saved: {', '.join(captured_fields) if captured_fields else 'minimal data'}")
            success_count += 1

        except Exception as e:
            log_message(f"  [FAIL] Database error: {e}")
            failed_count += 1

        time.sleep(2)

    log_message("")
    log_message("="*70)
    log_message("SUMMARY")
    log_message("="*70)
    log_message(f"Total stocks: {total}")
    log_message(f"Success: {success_count}")
    log_message(f"Failed: {failed_count}")
    log_message(f"Stocks with negative book value: {still_negative_bv}")
    log_message("")

    if success_count > 0:
        log_message("[SUCCESS] Re-scraping complete!")
        log_message("")
        log_message("Captured partial fundamentals even with negative book values:")
        log_message("  - Market Cap, Current Price, PE Ratio")
        log_message("  - Promoter Holdings, FII/DII Holdings")
        log_message("  - Week 52 High/Low, ROE, ROCE, EPS")
        log_message("")
        log_message("Next steps:")
        log_message("  1. Run: python scripts/rebuild/03_validate_database.py")
        log_message("  2. Should show ~2,183 fundamentals (2,103 + 80 recovered)")
        log_message("")

    conn.close()
    return success_count > 0

if __name__ == "__main__":
    try:
        ap = argparse.ArgumentParser()
        ap.add_argument("--all", action="store_true")
        ap.add_argument("--only-missing", action="store_true")
        ap.add_argument("--symbols-file")
        ap.add_argument("--limit", type=int, default=0)
        ap.add_argument("--dry-run", action="store_true")
        args = ap.parse_args()
        success = rescrape_failed_stocks(all_mode=args.all, only_missing=args.only_missing, limit=args.limit, symbols_file=args.symbols_file, dry_run=args.dry_run)
        exit(0 if success else 1)
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
