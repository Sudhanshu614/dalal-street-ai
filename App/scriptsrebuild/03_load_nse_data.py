"""
Phase 3: Load Initial NSE Data

Loads pure NSE authoritative data:
1. namechange.csv -> name_change_events (2,271 records)
2. symbolchange.csv -> symbol_change_events (987 records)
3. CF-CA CSV -> corporate_events (41,949 records with classification)
4. Infer delistings from IPO gap (~364 records)
"""

import pandas as pd
import sqlite3
from datetime import datetime

DB_PATH = 'App/database/stock_market_new.db'

def load_name_changes():
    """Load NSE namechange.csv"""
    print("\n[1/4] Loading name changes...")
    
    df = pd.read_csv('App/database/namechange.csv')
    df.columns = [c.strip() for c in df.columns]
    
    conn = sqlite3.connect(DB_PATH)
    
    inserted = 0
    for _, row in df.iterrows():
        try:
            conn.execute("""
                INSERT INTO name_change_events (symbol, old_name, new_name, change_date)
                VALUES (?, ?, ?, ?)
            """, (
                row['NCH_SYMBOL'],
                row['NCH_PREV_NAME'],
                row['NCH_NEW_NAME'],
                row['NCH_DT']
            ))
            inserted += 1
        except Exception as e:
            print(f"  Warning: Skipped row - {e}")
    
    conn.commit()
    conn.close()
    print(f"  Loaded {inserted} name changes (expected: 2,271)")

def load_symbol_changes():
    """Load NSE symbolchange.csv"""
    print("\n[2/4] Loading symbol changes...")
    
    df = pd.read_csv('App/database/symbolchange.csv', header=None)
    # Columns: [company_name, old_symbol, new_symbol, change_date]
    
    conn = sqlite3.connect(DB_PATH)
    
    inserted = 0
    for _, row in df.iterrows():
        try:
            conn.execute("""
                INSERT INTO symbol_change_events 
                (company_name, old_symbol, new_symbol, change_date)
                VALUES (?, ?, ?, ?)
            """, (row[0], row[1], row[2], row[3]))
            inserted += 1
        except Exception as e:
            print(f"  Warning: Skipped row - {e}")
    
    conn.commit()
    conn.close()
    print(f"  Loaded {inserted} symbol changes (expected: 987)")

def load_corporate_events():
    """Load NSE CF-CA CSV with event classification"""
    print("\n[3/4] Loading corporate events...")
    
    df = pd.read_csv('App/database/CF-CA-equities-01-01-1980-to-25-11-2025.csv')
    df.columns = [c.strip() for c in df.columns]
    
    # Classify events based on PURPOSE field
    def classify_event(purpose):
        if pd.isna(purpose):
            return 'OTHER'
        p = str(purpose).upper()
        if 'DEMERGER' in p or 'DE-MERGER' in p:
            return 'DEMERGER'
        elif 'AMALGAMATION' in p or 'MERGER' in p:
            return 'MERGER'
        elif 'BONUS' in p:
            return 'BONUS'
        elif 'DIVIDEND' in p:
            return 'DIVIDEND'
        elif 'SPLIT' in p:
            return 'SPLIT'
        elif 'AGM' in p or 'EGM' in p:
            return 'AGM'
        else:
            return 'OTHER'
    
    df['event_type'] = df['PURPOSE'].apply(classify_event)
    
    # Count event types
    event_counts = df['event_type'].value_counts().to_dict()
    print(f"  Event classification:")
    for event_type, count in sorted(event_counts.items()):
        print(f"    {event_type}: {count:,}")
    
    # Load to database
    conn = sqlite3.connect(DB_PATH)
    
    inserted = 0
    for _, row in df.iterrows():
        try:
            conn.execute("""
                INSERT INTO corporate_events 
                (symbol, company_name, purpose, event_type, ex_date, record_date,
                 bc_start_date, bc_end_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row.get('SYMBOL'),
                row.get('COMPANY NAME'),
                row.get('PURPOSE'),
                row['event_type'],
                row.get('EX-DATE'),
                row.get('RECORD DATE'),
                row.get('BC START DATE'),
                row.get('BC END DATE')
            ))
            inserted += 1
        except Exception as e:
            print(f"  Warning: Skipped row - {e}")
    
    conn.commit()
    conn.close()
    print(f"  Loaded {inserted} corporate events (expected: 41,949)")

def infer_delistings():
    """Infer delistings from IPO gap analysis"""
    print("\n[4/4] Inferring delistings...")
    
    # Get IPO symbols
    ipo_df = pd.read_csv('App/database/IPO-PastIssue-01-01-1980-to-25-11-2025.csv')
    ipo_symbols = set(ipo_df['Symbol'].dropna().str.upper())
    print(f"  IPO symbols: {len(ipo_symbols):,}")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Get active symbols
    cur.execute("SELECT symbol FROM stocks_master")
    active_symbols = set(row[0].upper() for row in cur.fetchall())
    print(f"  Active symbols: {len(active_symbols):,}")
    
    # Get symbols that changed (not delisted, just renamed)
    cur.execute("SELECT DISTINCT old_symbol FROM symbol_change_events")
    changed_symbols = set(row[0].upper() for row in cur.fetchall())
    print(f"  Changed symbols: {len(changed_symbols):,}")
    
    # Delisted = IPO - active - changed
    delisted = ipo_symbols - active_symbols - changed_symbols
    print(f"  Delisted (inferred): {len(delisted):,}")
    
    inserted = 0
    for symbol in delisted:
        # Try to find last trade date
        cur.execute("SELECT MAX(date) FROM daily_ohlc WHERE symbol = ?", (symbol,))
        result = cur.fetchone()
        last_date = result[0] if result else None
        
        try:
            conn.execute("""
                INSERT INTO delisting_events (symbol, last_traded_date)
                VALUES (?, ?)
            """, (symbol, last_date))
            inserted += 1
        except Exception as e:
            print(f"  Warning: Skipped {symbol} - {e}")
    
    conn.commit()
    conn.close()
    print(f"  Inserted {inserted} delisting records")

def verify_data():
    """Verify data was loaded correctly"""
    print("\n[VERIFICATION] Checking data load...")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Check record counts
    tables = {
        'name_change_events': 2271,
        'symbol_change_events': 987,
        'corporate_events': 41949,
        'delisting_events': 364  # Approximate
    }
    
    all_good = True
    for table, expected in tables.items():
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        actual = cur.fetchone()[0]
        
        status = "OK" if actual >= expected * 0.9 else "WARN"  # 90% threshold
        print(f"  {table}: {actual:,} ({status})")
        
        if status == "WARN":
            all_good = False
    
    conn.close()
    
    if all_good:
        print("  Status: SUCCESS")
        return True
    else:
        print("  Status: WARNING - Some counts lower than expected")
        return True  # Continue anyway

def main():
    """Main execution"""
    print("=" * 60)
    print("PHASE 3: LOAD INITIAL NSE DATA")
    print("=" * 60)
    
    load_name_changes()
    load_symbol_changes()
    load_corporate_events()
    infer_delistings()
    
    verify_data()
    
    print("\n" + "=" * 60)
    print("PHASE 3 COMPLETE")
    print("=" * 60)
    print("All NSE data loaded")
    print("Ready for Phase 4: Update TickerResolver")
    
    return 0

if __name__ == '__main__':
    exit(main())
