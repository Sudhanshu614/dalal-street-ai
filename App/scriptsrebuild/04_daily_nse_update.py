"""
Daily NSE Data Update Script

Keeps the ticker resolution system fresh by:
1. Updating stocks_master (new listings)
2. Updating name_change_events (incremental)
3. Updating symbol_change_events (incremental)
4. Reloading corporate_events (full refresh)
5. Updating delisting_events (inference)

Usage: python daily_nse_update.py
"""

import pandas as pd
import sqlite3
import os
from datetime import datetime
import shutil
import re

DB_PATH = 'App/database/stock_market_new.db'
CSV_DIR = 'App/database'

def update_stocks_master():
    print("\n[1/5] Updating stocks_master...")
    csv_path = os.path.join(CSV_DIR, 'stock_master.csv')
    if not os.path.exists(csv_path):
        print("  Error: stock_master.csv not found")
        return

    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]
    
    # Column keys in NSE file typically include 'name_of_company' and 'date_of_listing'
    name_key = 'name_of_company' if 'name_of_company' in df.columns else 'company_name'
    date_key = 'date_of_listing' if 'date_of_listing' in df.columns else 'listing_date'
    series_key = 'series' if 'series' in df.columns else None

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    cur.execute("SELECT symbol FROM stocks_master")
    existing = set(row[0] for row in cur.fetchall())

    present = set()
    added = 0
    updated = 0
    for _, row in df.iterrows():
        symbol = row.get('symbol')
        if not symbol:
            continue
        present.add(symbol)
        company_name = row.get(name_key)
        listing_date = row.get(date_key)
        series = row.get(series_key) if series_key else None
        try:
            if symbol in existing:
                cur.execute(
                    "UPDATE stocks_master SET company_name = COALESCE(?, company_name), is_active = 1, listing_date = COALESCE(?, listing_date) WHERE symbol = ?",
                    (company_name, listing_date, symbol)
                )
                updated += 1
            else:
                cur.execute(
                    "INSERT INTO stocks_master (symbol, company_name, is_active, listing_date) VALUES (?, ?, 1, ?)",
                    (symbol, company_name, listing_date)
                )
                added += 1
        except Exception as e:
            print(f"  Error upserting {symbol}: {e}")

    # Deactivate symbols no longer present
    to_deactivate = [sym for sym in existing if sym not in present]
    if to_deactivate:
        try:
            cur.executemany(
                "UPDATE stocks_master SET is_active = 0 WHERE symbol = ?",
                [(s,) for s in to_deactivate]
            )
            print(f"  Deactivated {len(to_deactivate)} symbols not in current stock_master.csv")
        except Exception as e:
            print(f"  Error deactivating: {e}")

    conn.commit()
    conn.close()
    print(f"  Added {added}, Updated {updated}")

def update_name_changes():
    """Incremental update of name changes"""
    print("\n[2/5] Updating name changes...")
    
    csv_path = os.path.join(CSV_DIR, 'namechange.csv')
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Get max date to filter new records
    cur.execute("SELECT MAX(change_date) FROM name_change_events")
    max_date = cur.fetchone()[0] or '1900-01-01'
    
    # Filter new records (simple date comparison)
    # Note: In production, better to use composite key check
    
    added = 0
    for _, row in df.iterrows():
        change_date = row['NCH_DT']
        # Simple check: if record doesn't exist
        cur.execute("""
            SELECT 1 FROM name_change_events 
            WHERE symbol=? AND old_name=? AND new_name=?
        """, (row['NCH_SYMBOL'], row['NCH_PREV_NAME'], row['NCH_NEW_NAME']))
        
        if not cur.fetchone():
            try:
                cur.execute("""
                    INSERT INTO name_change_events (symbol, old_name, new_name, change_date)
                    VALUES (?, ?, ?, ?)
                """, (row['NCH_SYMBOL'], row['NCH_PREV_NAME'], row['NCH_NEW_NAME'], change_date))
                added += 1
            except Exception:
                pass
                
    conn.commit()
    conn.close()
    print(f"  Added {added} new name changes")

def update_symbol_changes():
    """Incremental update of symbol changes"""
    print("\n[3/5] Updating symbol changes...")
    
    csv_path = os.path.join(CSV_DIR, 'symbolchange.csv')
    # No header in this file usually
    df = pd.read_csv(csv_path, header=None)
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    added = 0
    for _, row in df.iterrows():
        old_sym = row[1]
        new_sym = row[2]
        change_date = row[3]
        
        cur.execute("""
            SELECT 1 FROM symbol_change_events 
            WHERE old_symbol=? AND new_symbol=?
        """, (old_sym, new_sym))
        
        if not cur.fetchone():
            try:
                cur.execute("""
                    INSERT INTO symbol_change_events 
                    (company_name, old_symbol, new_symbol, change_date)
                    VALUES (?, ?, ?, ?)
                """, (row[0], old_sym, new_sym, change_date))
                added += 1
            except Exception:
                pass
    
    
    conn.commit()
    conn.close()
    print(f"  Added {added} new symbol changes")

def reload_corporate_events():
    """Full reload of corporate events (CF-CA)"""
    print("\n[4/5] Reloading corporate events...")
    
    # Find the latest CF-CA file using glob pattern
    from pathlib import Path
    csv_dir_path = Path(CSV_DIR)
    cf_ca_files = sorted(csv_dir_path.glob('CF-CA-equities-*.csv'))
    
    if not cf_ca_files:
        print("  Error: No CF-CA CSV file found (pattern: CF-CA-equities-*.csv)")
        return
    
    csv_path = cf_ca_files[-1]  # Get the latest file (sorted alphabetically)
    print(f"  Using: {csv_path.name}")
    
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    
    # Classify
    def classify_event(purpose):
        if pd.isna(purpose): return 'OTHER'
        p = str(purpose).upper()
        if 'DEMERGER' in p: return 'DEMERGER'
        elif 'AMALGAMATION' in p or 'MERGER' in p: return 'MERGER'
        elif 'BONUS' in p: return 'BONUS'
        elif 'SPLIT' in p: return 'SPLIT'
        elif 'DIVIDEND' in p: return 'DIVIDEND'
        elif 'AGM' in p: return 'AGM'
        else: return 'OTHER'
        
    df['event_type'] = df['PURPOSE'].apply(classify_event)
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Transaction for safety
    try:
        cur.execute("BEGIN TRANSACTION")
        cur.execute("DELETE FROM corporate_events")
        
        data = []
        for _, row in df.iterrows():
            data.append((
                row.get('SYMBOL'),
                row.get('COMPANY NAME'),
                row.get('PURPOSE'),
                row['event_type'],
                row.get('EX-DATE'),
                row.get('RECORD DATE'),
                row.get('BC START DATE'),
                row.get('BC END DATE')
            ))
            
        cur.executemany("""
            INSERT INTO corporate_events 
            (symbol, company_name, purpose, event_type, ex_date, record_date, bc_start_date, bc_end_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, data)
        
        cur.execute("COMMIT")
        print(f"  Reloaded {len(data)} events")
    except Exception as e:
        cur.execute("ROLLBACK")
        print(f"  Error reloading events: {e}")
    
    conn.close()

def update_delistings():
    print("\n[5/5] Updating delistings...")
    
    # Find the latest IPO file using glob pattern
    from pathlib import Path
    csv_dir_path = Path(CSV_DIR)
    ipo_files = sorted(csv_dir_path.glob('IPO-PastIssue-*.csv'))
    
    if not ipo_files:
        print("  IPO file not found; skipping")
        return
    
    ipo_path = ipo_files[-1]  # Get the latest file (sorted alphabetically)
    print(f"  Using: {ipo_path.name}")
    ipo = pd.read_csv(ipo_path)
    ipo.columns = [c.strip().lower().replace(' ', '_') for c in ipo.columns]
    sym_key = 'symbol' if 'symbol' in ipo.columns else 'security_code'
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT symbol FROM stocks_master WHERE is_active = 1")
    active = set(r[0] for r in cur.fetchall())
    cur.execute("SELECT DISTINCT old_symbol FROM symbol_change_events")
    old_syms = set(r[0] for r in cur.fetchall())
    inferred = 0
    for _, row in ipo.iterrows():
        sym = row.get(sym_key)
        if not sym:
            continue
        u = str(sym).strip().upper()
        if u not in active and u not in old_syms:
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO delisting_events (symbol, last_traded_date, delisting_reason) VALUES (?, ?, ?)",
                    (u, None, 'inferred_from_ipo_missing')
                )
                inferred += 1
            except Exception:
                pass
    conn.commit()
    conn.close()
    print(f"  Inferred {inferred} delistings")

def main():
    print("=" * 60)
    print("DAILY NSE DATA UPDATE")
    print("=" * 60)
    print(f"Time: {datetime.now()}")
    
    update_stocks_master()
    update_name_changes()
    update_symbol_changes()
    reload_corporate_events()
    update_delistings()
    
    print("\n" + "=" * 60)
    print("UPDATE COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()
