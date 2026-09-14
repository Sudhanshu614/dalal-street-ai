"""
PHASE 4: PROCESS STOCK ALIASES (Company Name Changes)
Processes 27 CSV files from BSE (1999-2025) containing company name changes

User provided: stock_aliases/Comp_Name_YYYY.csv (27 files, 2,673 total name changes)

CSV Format:
Security Code,Old Name,New Name,Date
539300,A.K. Spintex Ltd.,SUNRAKSHAKK INDUSTRIES INDIA LIMITED,23 May 2025

Strategy:
1. Read ALL CSV files (1999-2025)
2. Build comprehensive name change history
3. Match BSE names to NSE symbols using fuzzy matching
4. Generate symbol_mappings.py for AI chatbot
5. Create stock_aliases table in database

Relevance Question:
User asked: "i have the data from 1999, which i guess will not be that relevant"

Answer: OLD data IS relevant because:u
    python scripts/rebuild/04_process_stock_aliases.py
    python scripts/rebuild/04_process_stock_aliases.py --recent-only  # Last 5 years only
"""

import sqlite3
import pandas as pd
import difflib
from pathlib import Path
from datetime import datetime
import re
import argparse

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent.parent
ALIASES_DIR = PROJECT_ROOT / "database" / "stock_aliases"
DB_FILE = PROJECT_ROOT / "database" / "stock_market_new.db"
OUTPUT_FILE = PROJECT_ROOT / "data" / "symbol_mappings.py"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / f"04_process_aliases_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# Statistics
stats = {
    'total_csv_files': 0,
    'total_name_changes': 0,
    'matched_to_nse': 0,
    'not_found_nse': 0,
    'bse_only_stocks': 0,
    'duplicates_skipped': 0,
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

def clean_name(name):
    """Clean company name for matching"""
    if not name:
        return ""

    # Remove common suffixes
    cleaned = re.sub(r'\s+(Ltd\.?|Limited|Private|Pvt\.?|Corporation|Corp\.?|Inc\.?)$', '', str(name), flags=re.IGNORECASE)

    # Remove special characters
    cleaned = re.sub(r'[&\(\)\[\].,]', ' ', cleaned)

    # Normalize whitespace
    cleaned = ' '.join(cleaned.split())

    return cleaned.strip().upper()

def load_nse_symbols(conn):
    """Load NSE symbols and company names from database"""
    log_message("[INFO] Loading NSE symbols from database...")

    cursor = conn.cursor()
    cursor.execute("SELECT symbol, company_name FROM stocks_master WHERE is_active = 1")
    rows = cursor.fetchall()

    # Create mapping: {cleaned_name: (symbol, original_name)}
    nse_map = {}
    for symbol, company_name in rows:
        cleaned = clean_name(company_name)
        if cleaned:
            nse_map[cleaned] = (symbol, company_name)

    log_message(f"[OK] Loaded {len(nse_map)} NSE symbols")
    return nse_map

def find_nse_symbol(bse_name, nse_map, cutoff=0.85):
    """
    Find matching NSE symbol for BSE company name using fuzzy matching

    Args:
        bse_name: BSE company name
        nse_map: Dictionary of {cleaned_name: (symbol, original_name)}
        cutoff: Similarity threshold (0.85 = 85% match)

    Returns:
        (symbol, nse_name, confidence) or (None, None, 0)
    """
    cleaned_bse = clean_name(bse_name)

    if not cleaned_bse:
        return None, None, 0

    # Exact match first
    if cleaned_bse in nse_map:
        symbol, nse_name = nse_map[cleaned_bse]
        return symbol, nse_name, 1.0

    # Fuzzy match
    nse_names = list(nse_map.keys())
    matches = difflib.get_close_matches(cleaned_bse, nse_names, n=1, cutoff=cutoff)

    if matches:
        matched_name = matches[0]
        symbol, nse_name = nse_map[matched_name]

        # Calculate confidence
        confidence = difflib.SequenceMatcher(None, cleaned_bse, matched_name).ratio()

        return symbol, nse_name, confidence

    return None, None, 0

def load_all_csv_files(recent_only=False):
    """
    Load all CSV files from stock_aliases folder

    Args:
        recent_only: If True, load only last 5 years (2020-2025)

    Returns:
        DataFrame with all name changes
    """
    log_message("="*70)
    log_message("LOADING CSV FILES")
    log_message("="*70)

    if not ALIASES_DIR.exists():
        log_message(f"[ERROR] Directory not found: {ALIASES_DIR}")
        return None

    csv_files = sorted(ALIASES_DIR.glob("Comp_Name_*.csv"))

    if not csv_files:
        log_message(f"[ERROR] No CSV files found in {ALIASES_DIR}")
        return None

    log_message(f"[INFO] Found {len(csv_files)} CSV files")

    # Filter by year if recent_only
    if recent_only:
        current_year = datetime.now().year
        csv_files = [f for f in csv_files if int(f.stem.split('_')[-1]) >= current_year - 5]
        log_message(f"[INFO] Using recent files only (last 5 years): {len(csv_files)} files")

    all_data = []

    for csv_file in csv_files:
        year = csv_file.stem.split('_')[-1]

        try:
            df = pd.read_csv(csv_file)

            # Validate columns
            expected_cols = ['Security Code', 'Old Name', 'New Name', 'Date']
            if not all(col in df.columns for col in expected_cols):
                log_message(f"[WARN] {csv_file.name}: Missing columns, skipping")
                continue

            # Add year column
            df['Year'] = year

            all_data.append(df)

            stats['total_csv_files'] += 1
            stats['total_name_changes'] += len(df) - 1  # -1 for header

            log_message(f"[OK] {csv_file.name}: {len(df)} records")

        except Exception as e:
            log_message(f"[ERROR] {csv_file.name}: {e}")

    if not all_data:
        log_message("[ERROR] No valid CSV files loaded")
        return None

    combined_df = pd.concat(all_data, ignore_index=True)

    log_message("")
    log_message(f"[SUMMARY] Loaded {stats['total_csv_files']} CSV files")
    log_message(f"[SUMMARY] Total name changes: {stats['total_name_changes']}")
    log_message("")

    return combined_df

def process_name_changes(df, nse_map):
    """
    Process name changes and match to NSE symbols

    Returns:
        List of (old_name, new_name, nse_symbol, date, confidence)
    """
    log_message("="*70)
    log_message("MATCHING BSE NAMES TO NSE SYMBOLS")
    log_message("="*70)

    matched_records = []
    seen_mappings = set()  # Track duplicates

    for idx, row in df.iterrows():
        old_name = row['Old Name']
        new_name = row['New Name']
        date = row['Date']

        # Try to match NEW name first (current name more likely to be on NSE)
        symbol, nse_name, confidence = find_nse_symbol(new_name, nse_map)

        if symbol:
            # Check for duplicate
            mapping_key = (clean_name(old_name), symbol)
            if mapping_key in seen_mappings:
                stats['duplicates_skipped'] += 1
                continue

            seen_mappings.add(mapping_key)

            matched_records.append({
                'old_name': old_name,
                'new_name': new_name,
                'nse_symbol': symbol,
                'nse_name': nse_name,
                'date': date,
                'confidence': confidence
            })

            stats['matched_to_nse'] += 1

            if (idx + 1) % 100 == 0:
                log_message(f"[PROGRESS] {idx + 1}/{len(df)} processed, {stats['matched_to_nse']} matched")

        else:
            # Also try matching OLD name
            symbol, nse_name, confidence = find_nse_symbol(old_name, nse_map)

            if symbol:
                mapping_key = (clean_name(old_name), symbol)
                if mapping_key in seen_mappings:
                    stats['duplicates_skipped'] += 1
                    continue

                seen_mappings.add(mapping_key)

                matched_records.append({
                    'old_name': old_name,
                    'new_name': new_name,
                    'nse_symbol': symbol,
                    'nse_name': nse_name,
                    'date': date,
                    'confidence': confidence
                })

                stats['matched_to_nse'] += 1
            else:
                stats['not_found_nse'] += 1
                stats['bse_only_stocks'] += 1

    log_message("")
    log_message(f"[SUMMARY] Matched to NSE: {stats['matched_to_nse']}")
    log_message(f"[SUMMARY] Not found (BSE-only): {stats['not_found_nse']}")
    log_message(f"[SUMMARY] Duplicates skipped: {stats['duplicates_skipped']}")
    log_message("")

    return matched_records

def generate_symbol_mappings(matched_records):
    """Generate data/symbol_mappings.py file for AI chatbot"""
    log_message("="*70)
    log_message("GENERATING symbol_mappings.py")
    log_message("="*70)

    OUTPUT_FILE.parent.mkdir(exist_ok=True)

    # Sort by confidence (highest first)
    sorted_records = sorted(matched_records, key=lambda x: x['confidence'], reverse=True)

    content = '''"""
SYMBOL MAPPINGS - Company Name Changes (AUTO-GENERATED)
Maps old company names to current NSE symbols

Generated from: stock_aliases/Comp_Name_YYYY.csv (1999-2025)
Total mappings: {total}
Generated: {timestamp}

Usage in AI chatbot:
    from data.symbol_mappings import SYMBOL_MAPPINGS

    user_query = "What is the stock price of Aftek Business Machines?"
    old_name = "Aftek Business Machines"

    if old_name in SYMBOL_MAPPINGS:
        symbol = SYMBOL_MAPPINGS[old_name]
        # Use symbol to fetch data
"""

SYMBOL_MAPPINGS = {{
'''.format(
        total=len(sorted_records),
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )

    # Add mappings
    for record in sorted_records:
        old_name = record['old_name'].replace("'", "\\'")  # Escape quotes
        new_name = record['new_name'].replace("'", "\\'")
        symbol = record['nse_symbol']
        confidence = record['confidence']
        date = record['date']

        # Add both old and new names as keys
        content += f"    '{old_name}': '{symbol}',  # → {new_name} (confidence: {confidence:.2f}, date: {date})\n"

    content += "}\n\n"

    # Add reverse mapping (symbol → new name)
    content += "# Reverse mapping: symbol → current name\n"
    content += "SYMBOL_TO_NAME = {\n"

    # Get unique symbols
    unique_symbols = {}
    for record in sorted_records:
        symbol = record['nse_symbol']
        if symbol not in unique_symbols:
            unique_symbols[symbol] = record['new_name']

    for symbol, name in sorted(unique_symbols.items()):
        name_escaped = name.replace("'", "\\'")
        content += f"    '{symbol}': '{name_escaped}',\n"

    content += "}\n"

    # Write file
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    size_kb = OUTPUT_FILE.stat().st_size / 1024
    log_message(f"[OK] Generated: {OUTPUT_FILE}")
    log_message(f"[OK] Size: {size_kb:.2f} KB")
    log_message(f"[OK] Mappings: {len(sorted_records)}")
    log_message("")

def create_stock_aliases_table(conn, matched_records):
    """Create stock_aliases table in database (optional)"""
    log_message("="*70)
    log_message("CREATING stock_aliases TABLE (Optional)")
    log_message("="*70)

    cursor = conn.cursor()

    # Create table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_name TEXT NOT NULL,
            new_name TEXT NOT NULL,
            nse_symbol TEXT,
            change_date TEXT,
            confidence REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (nse_symbol) REFERENCES stocks_master(symbol)
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_aliases_old_name ON stock_aliases(old_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_aliases_symbol ON stock_aliases(nse_symbol)')

    # Insert records
    for record in matched_records:
        cursor.execute('''
            INSERT INTO stock_aliases (old_name, new_name, nse_symbol, change_date, confidence)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            record['old_name'],
            record['new_name'],
            record['nse_symbol'],
            record['date'],
            record['confidence']
        ))

    conn.commit()

    log_message(f"[OK] Created stock_aliases table with {len(matched_records)} records")
    log_message("")

def main():
    parser = argparse.ArgumentParser(description="Process stock aliases from CSV files")
    parser.add_argument('--recent-only', action='store_true',
                       help='Process only last 5 years (2020-2025) instead of all data')
    args = parser.parse_args()

    log_message("="*70)
    log_message("STOCK ALIASES PROCESSING")
    log_message("="*70)
    log_message(f"Mode: {'Recent only (2020-2025)' if args.recent_only else 'All years (1999-2025)'}")
    log_message(f"CSV Directory: {ALIASES_DIR}")
    log_message(f"Database: {DB_FILE}")
    log_message(f"Output: {OUTPUT_FILE}")
    log_message(f"Log: {LOG_FILE}")
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found. Run 02_master_rebuild.py first")
        return False

    # Load CSV files
    df = load_all_csv_files(recent_only=args.recent_only)
    if df is None or len(df) == 0:
        log_message("[ERROR] No data loaded")
        return False

    # Connect to database
    conn = sqlite3.connect(DB_FILE)

    try:
        # Load NSE symbols
        nse_map = load_nse_symbols(conn)

        # Match BSE names to NSE symbols
        matched_records = process_name_changes(df, nse_map)

        if not matched_records:
            log_message("[WARN] No matches found")
            return False

        # Generate symbol_mappings.py
        generate_symbol_mappings(matched_records)

        # Optionally create database table
        create_stock_aliases_table(conn, matched_records)

        # Final summary
        log_message("="*70)
        log_message("PROCESSING COMPLETE")
        log_message("="*70)
        log_message(f"CSV files processed: {stats['total_csv_files']}")
        log_message(f"Total name changes: {stats['total_name_changes']}")
        log_message(f"Matched to NSE: {stats['matched_to_nse']}")
        log_message(f"Not found (BSE-only): {stats['not_found_nse']}")
        log_message(f"Duplicates skipped: {stats['duplicates_skipped']}")
        log_message("")
        log_message(f"Output file: {OUTPUT_FILE}")
        log_message(f"Log file: {LOG_FILE}")
        log_message("")
        log_message("RELEVANCE NOTE:")
        log_message("  - Old data (1999) IS relevant for historical queries")
        log_message("  - Users might ask about companies that renamed 20+ years ago")
        log_message("  - AI needs complete history to answer questions about old companies")
        log_message("  - More mappings = better AI responses")
        log_message(f"  - All {stats['matched_to_nse']} mappings will help AI chatbot")
        log_message("")
        log_message("NEXT STEPS:")
        log_message("  1. Review: data/symbol_mappings.py")
        log_message("  2. Use in backend AI: from data.symbol_mappings import SYMBOL_MAPPINGS")
        log_message("  3. When user mentions old company name, map to current symbol")
        log_message("="*70)

        return True

    except Exception as e:
        log_message(f"[ERROR] {e}")
        import traceback
        log_message(traceback.format_exc())
        return False

    finally:
        conn.close()

if __name__ == "__main__":
    try:
        success = main()
        exit(0 if success else 1)
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
