"""
PHASE 10: SCRAPE IPO DATA FROM CHITTORGARH.COM
Scrapes comprehensive IPO data from Chittorgarh (2010-2025)

Creates new table: ipo_data

Data from: https://www.chittorgarh.com/ipo/ipo_perf_tracker.asp?year=YYYY
Coverage: Complete IPO history (2010-2025)

Usage:
    python scripts/rebuild/10_scrape_ipo_data_chittorgarh.py
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import time
import requests
from bs4 import BeautifulSoup
import re

DB_FILE = Path(__file__).parent.parent.parent / "database" / "stock_market_new.db"
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / f"10_ipo_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log_message(message):
    """Log to console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)

    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry + '\n')

def parse_number(text):
    """Parse Indian number format"""
    if not text:
        return None

    try:
        text = text.replace(',', '').replace('₹', '').strip()
        match = re.search(r'[-+]?\d*\.?\d+', text)
        if not match:
            return None
        return float(match.group())
    except:
        return None

def parse_percentage(text):
    """Parse percentage (e.g., '+125.5%' or '-12.3%')"""
    if not text:
        return None

    try:
        text = text.strip().replace('%', '').replace(',', '')
        match = re.search(r'[-+]?\d*\.?\d+', text)
        if match:
            return float(match.group())
        return None
    except:
        return None

def parse_date(text):
    """Parse date in format 'YYYY-MM-DD' or 'DD-MMM-YYYY'"""
    if not text:
        return None

    try:
        text = text.strip()
        # Try different date formats
        for fmt in ['%Y-%m-%d', '%d-%b-%Y', '%d-%m-%Y', '%d %b %Y']:
            try:
                dt = datetime.strptime(text, fmt)
                return dt.strftime('%Y-%m-%d')
            except:
                continue
        return None
    except:
        return None

def map_company_to_symbol(cursor, company_name):
    """
    Map IPO company name to stock symbol

    Uses multiple strategies:
    1. Exact match in stocks_master
    2. Partial match (LIKE query)
    3. Remove common suffixes and try again
    """
    if not company_name:
        return None

    # Strategy 1: Direct lookup
    cursor.execute("""
        SELECT symbol FROM stocks_master
        WHERE LOWER(company_name) = LOWER(?)
    """, (company_name,))

    result = cursor.fetchone()
    if result:
        return result[0]

    # Strategy 2: Remove common suffixes
    cleaned = company_name
    for suffix in [' Limited', ' Ltd.', ' Ltd', ' Pvt. Ltd.', ' Pvt Ltd', ' Private Limited']:
        cleaned = cleaned.replace(suffix, '')

    cleaned = cleaned.strip()

    # Try partial match
    cursor.execute("""
        SELECT symbol, company_name FROM stocks_master
        WHERE LOWER(company_name) LIKE LOWER(?)
        ORDER BY LENGTH(company_name) ASC
        LIMIT 1
    """, (f"%{cleaned}%",))

    result = cursor.fetchone()
    if result:
        return result[0]

    # Strategy 3: Try first few words
    words = cleaned.split()
    if len(words) >= 2:
        first_words = ' '.join(words[:2])
        cursor.execute("""
            SELECT symbol FROM stocks_master
            WHERE LOWER(company_name) LIKE LOWER(?)
            LIMIT 1
        """, (f"{first_words}%",))

        result = cursor.fetchone()
        if result:
            return result[0]

    return None

def create_ipo_table(conn):
    """Create ipo_data table if it doesn't exist"""
    cursor = conn.cursor()

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ipo_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                company_name TEXT NOT NULL,
                listing_date TEXT,
                issue_price REAL,
                listing_day_close REAL,
                listing_gains_percent REAL,
                current_price REAL,
                current_returns_percent REAL,

                data_source TEXT DEFAULT 'chittorgarh.com',
                last_updated TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(company_name, listing_date),
                FOREIGN KEY (symbol) REFERENCES stocks_master(symbol)
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ipo_symbol ON ipo_data(symbol)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ipo_date ON ipo_data(listing_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ipo_returns ON ipo_data(current_returns_percent)')

        conn.commit()
        log_message("[OK] ipo_data table created/verified")
        return True

    except Exception as e:
        log_message(f"[ERROR] Could not create table: {e}")
        return False

def scrape_chittorgarh_year(year):
    """
    Scrape IPO data from Chittorgarh for a specific year

    Returns: list of IPO records
    """
    url = f"https://www.chittorgarh.com/ipo/ipo_perf_tracker.asp?year={year}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    try:
        log_message(f"[INFO] Fetching {year} data...")
        response = requests.get(url, headers=headers, timeout=15)

        if response.status_code != 200:
            log_message(f"[ERROR] HTTP {response.status_code}")
            return []

        soup = BeautifulSoup(response.text, 'html.parser')

        # Find the IPO table
        table = soup.find('table', class_='table')
        if not table:
            table = soup.find('table')

        if not table:
            log_message(f"[WARN] No table found for {year}")
            return []

        # Parse table rows
        ipo_records = []
        tbody = table.find('tbody')
        if tbody:
            rows = tbody.find_all('tr')
        else:
            rows = table.find_all('tr')[1:]  # Skip header

        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 6:
                continue

            try:
                # Extract company name (first cell, usually has a link)
                company_cell = cells[0]
                company_name = company_cell.get_text(strip=True)

                if not company_name or company_name == '-':
                    continue

                # Extract other fields
                listing_date = parse_date(cells[1].get_text(strip=True)) if len(cells) > 1 else None
                issue_price = parse_number(cells[2].get_text(strip=True)) if len(cells) > 2 else None
                listing_day_close = parse_number(cells[3].get_text(strip=True)) if len(cells) > 3 else None
                listing_gains_text = cells[4].get_text(strip=True) if len(cells) > 4 else None
                listing_gains_percent = parse_percentage(listing_gains_text)
                current_price = parse_number(cells[5].get_text(strip=True)) if len(cells) > 5 else None
                current_returns_text = cells[6].get_text(strip=True) if len(cells) > 6 else None
                current_returns_percent = parse_percentage(current_returns_text)

                ipo_records.append({
                    'company_name': company_name,
                    'listing_date': listing_date,
                    'issue_price': issue_price,
                    'listing_day_close': listing_day_close,
                    'listing_gains_percent': listing_gains_percent,
                    'current_price': current_price,
                    'current_returns_percent': current_returns_percent,
                })

            except Exception as e:
                log_message(f"[WARN] Failed to parse row: {e}")
                continue

        log_message(f"[OK] Found {len(ipo_records)} IPOs for {year}")
        return ipo_records

    except Exception as e:
        log_message(f"[ERROR] Scraping failed for {year}: {e}")
        return []

def scrape_all_ipo_data():
    log_message("="*70)
    log_message("SCRAPE IPO DATA FROM CHITTORGARH.COM")
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

        # Scrape data year by year (2010-2025)
        years = range(2010, 2026)  # 2010 to 2025
        all_ipo_records = []

        for year in years:
            records = scrape_chittorgarh_year(year)
            all_ipo_records.extend(records)
            time.sleep(2)  # Rate limiting

        if not all_ipo_records:
            log_message("[ERROR] No IPO data scraped")
            return False

        log_message("")
        log_message(f"[INFO] Total IPO records scraped: {len(all_ipo_records)}")
        log_message(f"[INFO] Mapping company names to symbols...")
        log_message("")

        # Insert into database with symbol mapping
        inserted = 0
        updated = 0
        mapped = 0
        unmapped = 0

        for i, record in enumerate(all_ipo_records, 1):
            # Try to map company name to symbol
            symbol = map_company_to_symbol(cursor, record['company_name'])

            if symbol:
                mapped += 1
                if mapped <= 10:  # Show first 10 mappings
                    log_message(f"[MAP] {record['company_name'][:40]} → {symbol}")
            else:
                unmapped += 1
                if unmapped <= 5:  # Show first 5 unmapped
                    log_message(f"[SKIP] Could not map: {record['company_name'][:50]}")

            try:
                cursor.execute('''
                    INSERT OR REPLACE INTO ipo_data
                    (symbol, company_name, listing_date, issue_price, listing_day_close,
                     listing_gains_percent, current_price, current_returns_percent, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symbol,
                    record['company_name'],
                    record['listing_date'],
                    record['issue_price'],
                    record['listing_day_close'],
                    record['listing_gains_percent'],
                    record['current_price'],
                    record['current_returns_percent'],
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))

                inserted += 1

            except Exception as e:
                log_message(f"[ERROR] Failed to insert {record['company_name']}: {e}")
                continue

        conn.commit()

        log_message("")
        log_message("="*70)
        log_message("SUMMARY")
        log_message("="*70)
        log_message(f"Total records scraped: {len(all_ipo_records)}")
        log_message(f"Inserted to database: {inserted}")
        log_message(f"Mapped to symbols: {mapped} ({mapped/len(all_ipo_records)*100:.1f}%)")
        log_message(f"Unmapped (stored with company name): {unmapped}")
        log_message("")
        log_message(f"[SUCCESS] IPO data scraping complete!")
        log_message(f"Log file: {LOG_FILE}")
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
        success = scrape_all_ipo_data()
        exit(0 if success else 1)
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
