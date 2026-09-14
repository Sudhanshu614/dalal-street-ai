"""
PHASE 8: SCRAPE ENHANCED FUNDAMENTALS
Scrapes additional fields from Screener.in and updates existing fundamentals

New data being scraped:
A. Industry/Sector Classification (from Peer Comparison section)
B. Returns Data (calculated from historical prices or scraped)
C. Growth Metrics (from Compounded Growth section)
D. Debt to Equity (calculated from Balance Sheet)
E. Promoter Pledge % (from Shareholding Pattern)

Updates EXISTING fundamentals records only (no new records created)

Usage:
    python scripts/rebuild/08_scrape_enhanced_fundamentals.py
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

LOG_FILE = LOG_DIR / f"08_enhanced_fundamentals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log_message(message):
    """Log to console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)

    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry + '\n')

def parse_number(text):
    """Parse Indian number format (e.g., '1,234.56 Cr.' → 1234560000)"""
    if not text:
        return None

    try:
        # Remove commas and convert to lowercase
        text = text.replace(',', '').lower().strip()

        # Extract number (including negative)
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
        text = text.strip().replace('%', '').replace(',', '')
        match = re.search(r'[-+]?\d*\.?\d+', text)
        if match:
            return float(match.group())
        return None
    except:
        return None

def scrape_enhanced_data(symbol):
    """
    Scrape enhanced fundamental data from Screener.in

    Returns: dict with new fields, or None if error
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

        data = {'symbol': symbol}

        # ================================================================
        # A. INDUSTRY/SECTOR CLASSIFICATION (from Peer Comparison)
        # ================================================================
        peer_section = soup.find('section', {'id': 'peers'})
        if peer_section:
            # Look for market category links (first 4 links are the hierarchy)
            # Example: Energy > Oil, Gas & Consumable Fuels > Petroleum Products > Refineries & Marketing
            all_links = peer_section.find_all('a', href=True)
            categories = []

            for link in all_links:
                href = link.get('href', '')
                # Market category links have pattern: /market/IN03/ or /market/IN03/IN0301/
                if '/market/IN' in href and len(categories) < 4:
                    category = link.get_text(strip=True)
                    if category and category not in categories:
                        categories.append(category)

            # Assign categories to fields (reverse order: Sector > Industry > Subsector > Segment)
            if len(categories) >= 1:
                data['sector'] = categories[0]  # Top level (e.g., "Energy")
            if len(categories) >= 2:
                data['industry'] = categories[1]  # Second level (e.g., "Oil, Gas & Consumable Fuels")
            if len(categories) >= 3:
                data['subsector'] = categories[2]  # Third level (e.g., "Petroleum Products")
            if len(categories) >= 4:
                data['business_segment'] = categories[3]  # Fourth level (e.g., "Refineries & Marketing")

        # ================================================================
        # B. GROWTH METRICS (from Compounded Growth section)
        # ================================================================
        # Screener.in has separate tables for Sales Growth and Profit Growth
        # Structure: Row 1=Header, Row 2="10 Years: | 10%", Row 3="5 Years: | 10%", etc.

        for table in soup.find_all('table'):
            headers = table.find_all('th')
            if not headers:
                continue

            header_text = headers[0].get_text(strip=True) if headers else ''

            # Check for "Compounded Sales Growth" table
            if 'Compounded Sales Growth' in header_text:
                rows = table.find_all('tr')
                for row in rows[1:]:  # Skip header row
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        period = cells[0].get_text(strip=True)
                        value = cells[1].get_text(strip=True)

                        if '10 Years' in period or '10 Year' in period:
                            data['sales_growth_10year'] = parse_percentage(value)
                        elif '5 Years' in period or '5 Year' in period:
                            data['sales_growth_5year'] = parse_percentage(value)
                        elif '3 Years' in period or '3 Year' in period:
                            data['sales_growth_3year'] = parse_percentage(value)

            # Check for "Compounded Profit Growth" table
            elif 'Compounded Profit Growth' in header_text:
                rows = table.find_all('tr')
                for row in rows[1:]:  # Skip header row
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        period = cells[0].get_text(strip=True)
                        value = cells[1].get_text(strip=True)

                        if '10 Years' in period or '10 Year' in period:
                            data['profit_growth_10year'] = parse_percentage(value)
                        elif '5 Years' in period or '5 Year' in period:
                            data['profit_growth_5year'] = parse_percentage(value)
                        elif '3 Years' in period or '3 Year' in period:
                            data['profit_growth_3year'] = parse_percentage(value)

        # ================================================================
        # C. RETURNS DATA (from Stock Price chart section)
        # ================================================================
        # Screener.in shows returns in chart tooltips, but not easily scrapable
        # We'll calculate these from daily_ohlc table later (Phase 9)
        # For now, leave as NULL

        # ================================================================
        # D. DEBT TO EQUITY (from Balance Sheet)
        # Special handling for banks: Extract Deposits instead of D/E
        # ================================================================
        balance_sheet = soup.find('section', {'id': 'balance-sheet'})
        if balance_sheet:
            # Check for pre-calculated ratio
            ratio_text = balance_sheet.find(string=re.compile('Debt to equity', re.IGNORECASE))
            if ratio_text:
                parent = ratio_text.parent
                if parent:
                    value_span = parent.find_next('span', class_='number')
                    if value_span:
                        data['debt_to_equity'] = parse_number(value_span.get_text(strip=True))

            # If not found, try to calculate from table
            # Formula: Debt/Equity = Borrowings / (Equity Capital + Reserves)
            # For banks: Extract Deposits instead
            if 'debt_to_equity' not in data:
                table = balance_sheet.find('table')
                if table:
                    rows = table.find_all('tr')
                    borrowings = None
                    equity_capital = None
                    reserves = None
                    deposits = None
                    is_bank = False

                    for row in rows:
                        cells = row.find_all(['td', 'th'])
                        if len(cells) >= 2:
                            label = cells[0].get_text(strip=True)
                            value = cells[-1].get_text(strip=True)  # Latest value (last column)

                            # Check if this is a bank (has Deposits row)
                            if 'Deposits' in label and 'Fixed' not in label:
                                is_bank = True
                                deposits = parse_number(value)

                            # Look for Borrowings (debt)
                            if 'Borrowings' in label:
                                borrowings = parse_number(value)

                            # Look for Equity Capital
                            elif 'Equity Capital' in label:
                                equity_capital = parse_number(value)

                            # Look for Reserves
                            elif 'Reserves' in label and 'Revaluation' not in label:
                                reserves = parse_number(value)

                    # For banks: Store deposits instead of D/E ratio
                    if is_bank and deposits is not None:
                        data['total_deposits'] = deposits
                        # Don't calculate D/E for banks (not meaningful)

                    # For non-banks: Calculate D/E if we have all components
                    elif borrowings is not None and equity_capital is not None and reserves is not None:
                        total_equity = equity_capital + reserves
                        if total_equity > 0:
                            data['debt_to_equity'] = round(borrowings / total_equity, 2)

        # ================================================================
        # E. MANAGEMENT QUALITY (SKIPPED)
        # ================================================================
        # Promoter Pledge % - Not always available, low coverage
        # Piotroski Score - Not on Screener.in, would need manual calculation
        # DECISION: Skip this for now (user requested)

        return data, None

    except Exception as e:
        return None, str(e)

def scrape_all_enhanced_fundamentals():
    log_message("="*70)
    log_message("SCRAPE ENHANCED FUNDAMENTALS")
    log_message("="*70)
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found")
        return False

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    try:
        # Get all stocks that have fundamentals
        cursor.execute("SELECT symbol FROM fundamentals ORDER BY symbol")
        stocks = [row[0] for row in cursor.fetchall()]

        total = len(stocks)
        log_message(f"[INFO] Found {total} stocks with fundamentals")
        log_message(f"[INFO] Scraping enhanced data from Screener.in...")
        log_message("")

        success_count = 0
        failed_count = 0
        not_found_count = 0

        for i, symbol in enumerate(stocks, 1):
            log_message(f"[{i}/{total}] {symbol}...")

            # Scrape enhanced data
            data, error = scrape_enhanced_data(symbol)

            if error:
                if error == "NOT_FOUND":
                    log_message(f"  [SKIP] Not found on Screener.in")
                    not_found_count += 1
                else:
                    log_message(f"  [FAIL] {error}")
                    failed_count += 1
                time.sleep(2)
                continue

            if not data:
                log_message(f"  [FAIL] No data returned")
                failed_count += 1
                time.sleep(2)
                continue

            # Update database with new fields
            update_fields = []
            update_values = []

            field_mapping = {
                'industry': data.get('industry'),
                'sector': data.get('sector'),
                'subsector': data.get('subsector'),
                'business_segment': data.get('business_segment'),
                'sales_growth_3year': data.get('sales_growth_3year'),
                'sales_growth_5year': data.get('sales_growth_5year'),
                'sales_growth_10year': data.get('sales_growth_10year'),
                'profit_growth_3year': data.get('profit_growth_3year'),
                'profit_growth_5year': data.get('profit_growth_5year'),
                'profit_growth_10year': data.get('profit_growth_10year'),
                'debt_to_equity': data.get('debt_to_equity'),
                'total_deposits': data.get('total_deposits'),
                'pledged_percentage': data.get('pledged_percentage'),
            }

            for field, value in field_mapping.items():
                if value is not None:
                    update_fields.append(f"{field} = ?")
                    update_values.append(value)

            if update_fields:
                update_values.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                update_values.append(symbol)

                sql = f"""
                    UPDATE fundamentals
                    SET {', '.join(update_fields)}, last_updated = ?
                    WHERE symbol = ?
                """

                cursor.execute(sql, update_values)
                conn.commit()

                # Show what was captured
                captured = []
                if data.get('industry'):
                    captured.append(f"Industry: {data['industry']}")
                if data.get('sales_growth_3year'):
                    captured.append(f"Sales Growth 3Y: {data['sales_growth_3year']:.1f}%")
                if data.get('debt_to_equity'):
                    captured.append(f"D/E: {data['debt_to_equity']:.2f}")
                if data.get('total_deposits'):
                    captured.append(f"Deposits: {data['total_deposits']/10000000:.0f} Cr")

                log_message(f"  [OK] {', '.join(captured) if captured else 'Some fields updated'}")
                success_count += 1
            else:
                log_message(f"  [SKIP] No new data found")
                success_count += 1

            # Rate limiting (be nice to Screener.in)
            time.sleep(2)

        log_message("")
        log_message("="*70)
        log_message("SUMMARY")
        log_message("="*70)
        log_message(f"Total stocks: {total}")
        log_message(f"Success: {success_count}")
        log_message(f"Failed: {failed_count}")
        log_message(f"Not found: {not_found_count}")
        log_message("")
        log_message(f"[SUCCESS] Enhanced fundamentals scraping complete!")
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
        success = scrape_all_enhanced_fundamentals()
        exit(0 if success else 1)
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
