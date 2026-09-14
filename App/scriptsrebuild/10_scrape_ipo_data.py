"""
PHASE 10: SCRAPE IPO DATA FROM SCREENER.IN
Scrapes IPO data (listing date, issue price, listing price, gains)

Creates new table: ipo_data

Data from: https://www.screener.in/ipo/recent/
Coverage: ~942 companies from last 3 years (2022-2025)

Usage:
    python scripts/rebuild/10_scrape_ipo_data.py
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import time
import requests
from bs4 import BeautifulSoup
import re

DB_FILE = Path(__file__).parent.parent.parent / "database" / "stock_market_new.db"

def log_message(message):
    print(message)

def parse_number(text):
    """Parse Indian number format"""
    if not text:
        return None

    try:
        text = text.replace(',', '').lower().strip()
        match = re.search(r'[-+]?\d*\.?\d+', text)
        if not match:
            return None

        num = float(match.group())

        if 'cr' in text or 'crore' in text:
            num *= 10000000
        elif 'lac' in text or 'lakh' in text:
            num *= 100000

        return num
    except:
        return None

def create_ipo_table(conn):
    """Create ipo_data table if it doesn't exist"""
    cursor = conn.cursor()

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ipo_data (
                symbol TEXT PRIMARY KEY,
                company_name TEXT,
                listing_date TEXT,
                ipo_price REAL,
                listing_price REAL,
                current_price REAL,
                listing_gains_percent REAL,
                current_returns_percent REAL,
                ipo_market_cap REAL,

                data_source TEXT DEFAULT 'screener.in',
                last_updated TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (symbol) REFERENCES stocks_master(symbol)
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ipo_date ON ipo_data(listing_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ipo_gains ON ipo_data(listing_gains_percent)')

        conn.commit()
        log_message("[OK] ipo_data table created/verified")
        return True

    except Exception as e:
        log_message(f"[ERROR] Could not create table: {e}")
        return False

def scrape_ipo_page():
    """
    Scrape IPO data from Screener.in

    Returns: list of IPO records
    """
    url = "https://www.screener.in/ipo/recent/"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    try:
        log_message(f"[INFO] Fetching {url}")
        response = requests.get(url, headers=headers, timeout=15)

        if response.status_code != 200:
            log_message(f"[ERROR] HTTP {response.status_code}")
            return []

        soup = BeautifulSoup(response.text, 'html.parser')

        # Find the IPO table
        table = soup.find('table', class_='data-table')
        if not table:
            # Try alternative selector
            table = soup.find('table')

        if not table:
            log_message("[ERROR] Could not find IPO table")
            return []

        # Parse table headers
        headers_row = table.find('thead')
        if headers_row:
            headers = [th.get_text(strip=True) for th in headers_row.find_all('th')]
            log_message(f"[INFO] Table columns: {headers}")

        # Parse table rows
        ipo_records = []
        tbody = table.find('tbody')
        if tbody:
            rows = tbody.find_all('tr')
        else:
            rows = table.find_all('tr')[1:]  # Skip header row

        log_message(f"[INFO] Found {len(rows)} IPO records")

        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 5:
                continue

            try:
                # Extract company name and symbol
                company_cell = cells[0]
                company_link = company_cell.find('a')
                if company_link:
                    company_name = company_link.get_text(strip=True)
                    # Extract symbol from URL: /company/SYMBOL/
                    href = company_link.get('href', '')
                    symbol_match = re.search(r'/company/([A-Z0-9-]+)/', href)
                    symbol = symbol_match.group(1) if symbol_match else None
                else:
                    company_name = company_cell.get_text(strip=True)
                    symbol = None

                if not symbol:
                    continue

                # Extract other fields
                listing_date = cells[1].get_text(strip=True) if len(cells) > 1 else None
                ipo_market_cap = parse_number(cells[2].get_text(strip=True)) if len(cells) > 2 else None
                ipo_price = parse_number(cells[3].get_text(strip=True)) if len(cells) > 3 else None
                current_price = parse_number(cells[4].get_text(strip=True)) if len(cells) > 4 else None

                # Parse percentage change (e.g., "+125.5%" or "-12.3%")
                change_text = cells[5].get_text(strip=True) if len(cells) > 5 else None
                current_returns_percent = None
                if change_text:
                    match = re.search(r'([-+]?\d+\.?\d*)', change_text.replace('%', ''))
                    if match:
                        current_returns_percent = float(match.group(1))

                # Calculate listing gains (we don't have listing price from this page)
                # Will be NULL for now
                listing_price = None
                listing_gains_percent = None

                ipo_records.append({
                    'symbol': symbol,
                    'company_name': company_name,
                    'listing_date': listing_date,
                    'ipo_price': ipo_price,
                    'listing_price': listing_price,
                    'current_price': current_price,
                    'listing_gains_percent': listing_gains_percent,
                    'current_returns_percent': current_returns_percent,
                    'ipo_market_cap': ipo_market_cap,
                })

            except Exception as e:
                log_message(f"[WARN] Failed to parse row: {e}")
                continue

        return ipo_records

    except Exception as e:
        log_message(f"[ERROR] Scraping failed: {e}")
        import traceback
        traceback.print_exc()
        return []

def scrape_ipo_data():
    log_message("="*70)
    log_message("SCRAPE IPO DATA FROM SCREENER.IN")
    log_message("="*70)
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found")
        return False

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    try:
        # Create table
        if not create_ipo_table(conn):
            return False

        log_message("")

        # Scrape IPO data
        ipo_records = scrape_ipo_page()

        if not ipo_records:
            log_message("[ERROR] No IPO data scraped")
            return False

        log_message(f"[INFO] Scraped {len(ipo_records)} IPO records")
        log_message("")

        # Insert into database
        inserted = 0
        updated = 0
        skipped = 0

        for record in ipo_records:
            try:
                cursor.execute('''
                    INSERT OR REPLACE INTO ipo_data
                    (symbol, company_name, listing_date, ipo_price, listing_price,
                     current_price, listing_gains_percent, current_returns_percent,
                     ipo_market_cap, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    record['symbol'],
                    record['company_name'],
                    record['listing_date'],
                    record['ipo_price'],
                    record['listing_price'],
                    record['current_price'],
                    record['listing_gains_percent'],
                    record['current_returns_percent'],
                    record['ipo_market_cap'],
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))

                if cursor.rowcount > 0:
                    inserted += 1
                    if inserted <= 10:  # Show first 10
                        log_message(f"[{inserted}] {record['symbol']}: {record['company_name']} (Listed: {record['listing_date']}, Returns: {record['current_returns_percent']:+.1f}%)")
                else:
                    updated += 1

            except Exception as e:
                log_message(f"[ERROR] {record['symbol']}: {e}")
                skipped += 1

        conn.commit()

        log_message("")
        log_message("="*70)
        log_message("SUMMARY")
        log_message("="*70)
        log_message(f"Total records scraped: {len(ipo_records)}")
        log_message(f"Inserted: {inserted}")
        log_message(f"Updated: {updated}")
        log_message(f"Skipped: {skipped}")
        log_message("")
        log_message(f"[SUCCESS] IPO data scraping complete!")
        log_message("")

        return True

    except Exception as e:
        log_message(f"[ERROR] Scraping failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        conn.close()

if __name__ == "__main__":
    try:
        success = scrape_ipo_data()
        exit(0 if success else 1)
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
