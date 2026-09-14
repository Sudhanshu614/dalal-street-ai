import sqlite3
import pandas as pd
import re
from pathlib import Path
from datetime import datetime

def clean(s):
    x = str(s or '')
    x = re.sub(r"\s+(Ltd\.?|Limited|Private|Pvt\.?|Corporation|Corp\.?|Inc\.?)$", "", x, flags=re.IGNORECASE)
    x = re.sub(r"[&()\[\].,]", " ", x)
    x = " ".join(x.split())
    return x.strip().upper()

def norm_cols(df):
    def m(s):
        s=str(s).strip().lower()
        if 'symbol' in s:
            return 'SYMBOL'
        if 'company' in s and 'name' in s:
            return 'COMPANY_NAME'
        if 'isin' in s:
            return 'ISIN'
        if 'series' in s:
            return 'SERIES'
        if 'face' in s and 'value' in s:
            return 'FACE_VALUE'
        if 'date' in s and 'list' in s:
            return 'DATE_OF_LISTING'
        return s
    return df.rename(columns={c:m(c) for c in df.columns})

def ensure_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_master_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            company_name TEXT,
            isin TEXT,
            series TEXT,
            face_value REAL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alias_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_symbol TEXT,
            new_symbol TEXT,
            old_name TEXT,
            new_name TEXT,
            old_isin TEXT,
            new_isin TEXT,
            effective_date TEXT,
            source TEXT,
            confidence REAL,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

def main():
    db = Path('App/database/stock_market_new.db').resolve()
    csv_path = Path('App/database/stock_master.csv').resolve()
    today = datetime.now().strftime('%Y-%m-%d')
    df = pd.read_csv(csv_path)
    df = norm_cols(df)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass
    ensure_tables(conn)
    cur = conn.cursor()

    # write snapshot
    inserts = 0
    for _, row in df.iterrows():
        sym = str(row.get('SYMBOL') or '').strip().upper()
        if not sym:
            continue
        cur.execute(
            """
            INSERT INTO stock_master_snapshot (snapshot_date, symbol, company_name, isin, series, face_value)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (today,
             sym,
             str(row.get('COMPANY_NAME') or ''),
             str(row.get('ISIN') or ''),
             str(row.get('SERIES') or ''),
             row.get('FACE_VALUE'))
        )
        inserts += 1
    conn.commit()

    # load previous snapshot
    cur.execute("SELECT MAX(snapshot_date) AS d FROM stock_master_snapshot WHERE snapshot_date < ?", (today,))
    prev = cur.fetchone()
    prev_date = prev['d'] if prev and prev['d'] else None
    events_created = 0
    samples = []
    if prev_date:
        cur.execute("SELECT symbol, company_name, isin FROM stock_master_snapshot WHERE snapshot_date = ?", (prev_date,))
        prev_rows = cur.fetchall()
        prev_by_symbol = {r['symbol'].upper(): r for r in prev_rows}
        prev_by_name = {clean(r['company_name']): r for r in prev_rows}

        cur.execute("SELECT symbol, company_name, isin FROM stock_master_snapshot WHERE snapshot_date = ?", (today,))
        curr_rows = cur.fetchall()
        for r in curr_rows:
            sym = (r['symbol'] or '').upper()
            name = r['company_name'] or ''
            isin = (r['isin'] or '')
            prev_r = prev_by_symbol.get(sym)
            if prev_r:
                # name change
                if clean(prev_r['company_name']) != clean(name):
                    cur.execute(
                        """
                        INSERT INTO alias_events (old_symbol, new_symbol, old_name, new_name, old_isin, new_isin, effective_date, source, confidence, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (sym, sym, prev_r['company_name'], name, prev_r['isin'], isin, today, 'csv_diff', 0.9, 'name_change')
                    )
                    events_created += 1
                    if len(samples) < 5:
                        samples.append({'type':'name_change','symbol':sym})
                # isin change
                if (prev_r['isin'] or '') != (isin or '') and clean(prev_r['company_name']) == clean(name):
                    cur.execute(
                        """
                        INSERT INTO alias_events (old_symbol, new_symbol, old_name, new_name, old_isin, new_isin, effective_date, source, confidence, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (sym, sym, name, name, prev_r['isin'], isin, today, 'csv_diff', 0.85, 'isin_change')
                    )
                    events_created += 1
                    if len(samples) < 5:
                        samples.append({'type':'isin_change','symbol':sym})
            else:
                # possible symbol change: match by name
                prev_name_r = prev_by_name.get(clean(name))
                if prev_name_r and prev_name_r['symbol'].upper() != sym:
                    cur.execute(
                        """
                        INSERT INTO alias_events (old_symbol, new_symbol, old_name, new_name, old_isin, new_isin, effective_date, source, confidence, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (prev_name_r['symbol'].upper(), sym, prev_name_r['company_name'], name, prev_name_r['isin'], isin, today, 'csv_diff', 0.8, 'symbol_change')
                    )
                    events_created += 1
                    if len(samples) < 5:
                        samples.append({'type':'symbol_change','from':prev_name_r['symbol'].upper(),'to':sym})
        conn.commit()

    print({'snapshot_rows': inserts, 'events_created': events_created, 'samples': samples})
    conn.close()
    return 0

if __name__=='__main__':
    raise SystemExit(main())