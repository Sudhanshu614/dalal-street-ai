import sqlite3
import pandas as pd
import difflib
import re
from pathlib import Path
from datetime import datetime

def clean(s):
    x = str(s or '')
    x = re.sub(r"\s+(Ltd\.?|Limited|Private|Pvt\.?|Corporation|Corp\.?|Inc\.?)$", "", x, flags=re.IGNORECASE)
    x = re.sub(r"[&()\[\].,]", " ", x)
    x = " ".join(x.split())
    return x.strip().upper()

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    def m(s):
        s=str(s).strip().lower()
        if 'old' in s and 'name' in s:
            return 'OLD_NAME'
        if 'new' in s and 'name' in s:
            return 'NEW_NAME'
        if 'effective' in s or ('date' in s and ('change' in s or 'eff' in s)):
            return 'EFFECTIVE_DATE'
        if 'symbol' in s:
            return 'SYMBOL'
        if 'isin' in s:
            return 'ISIN'
        return s
    return df.rename(columns={c:m(c) for c in df.columns})

def ensure_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS name_changes_raw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_name TEXT,
            new_name TEXT,
            change_date TEXT,
            source TEXT,
            extras TEXT,
            mapped INTEGER DEFAULT 0,
            skip_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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

def build_canonical_maps(conn):
    cur = conn.cursor()
    cur.execute("SELECT symbol, company_name, isin, is_active FROM stocks_master")
    rows = cur.fetchall()
    by_symbol = {}
    by_isin = {}
    by_name = {}
    for r in rows:
        sym = (r[0] if isinstance(r, tuple) else r['symbol']).upper()
        name = r[1] if isinstance(r, tuple) else r['company_name']
        isin = (r[2] if isinstance(r, tuple) else r['isin']) or ''
        active = r[3] if isinstance(r, tuple) else r['is_active']
        rec = {'symbol': sym, 'company_name': name, 'isin': isin, 'is_active': active}
        by_symbol[sym] = rec
        if isin:
            by_isin[isin.upper()] = rec
        if name:
            by_name[clean(name)] = rec
    return by_symbol, by_isin, by_name

def map_row(row, by_symbol, by_isin, by_name):
    old_name = str(row.get('OLD_NAME') or '').strip()
    new_name = str(row.get('NEW_NAME') or '').strip()
    eff = str(row.get('EFFECTIVE_DATE') or '').strip()
    symbol_hint = str(row.get('SYMBOL') or '').strip().upper()
    isin_val = str(row.get('ISIN') or '').strip().upper()
    # 1) ISIN join
    if isin_val and isin_val in by_isin:
        rec = by_isin[isin_val]
        return {'new_symbol': rec['symbol'], 'new_isin': rec['isin'], 'confidence': 0.95, 'notes': 'isin_join', 'company_name': rec.get('company_name')}
    # 2) symbol hint
    if symbol_hint and symbol_hint in by_symbol:
        rec = by_symbol[symbol_hint]
        return {'new_symbol': rec['symbol'], 'new_isin': rec['isin'], 'confidence': 0.90, 'notes': 'symbol_hint', 'company_name': rec.get('company_name')}
    # 3) name fuzzy
    cn = clean(new_name)
    if cn and by_name:
        names = list(by_name.keys())
        matches = difflib.get_close_matches(cn, names, n=1, cutoff=0.90)
        if matches:
            m = matches[0]
            rec = by_name[m]
            sim = difflib.SequenceMatcher(None, cn, m).ratio()
            return {'new_symbol': rec['symbol'], 'new_isin': rec['isin'], 'confidence': sim, 'notes': 'name_fuzzy', 'company_name': rec.get('company_name')}
    return None

def main():
    db = Path('App/database/stock_market_new.db').resolve()
    xlsx = Path('App/database/Company_Name_Changes_NSE.xlsx').resolve()
    sheets = pd.read_excel(xlsx, sheet_name=None)
    frames = [normalize_cols(df) for df in sheets.values() if isinstance(df, pd.DataFrame) and not df.empty]
    if not frames:
        print({'error':'no_excel_rows'})
        return 1
    df = pd.concat(frames, ignore_index=True)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass
    ensure_tables(conn)
    by_symbol, by_isin, by_name = build_canonical_maps(conn)
    cur = conn.cursor()
    mapped = 0
    skipped = 0
    samples = []
    for _, row in df.iterrows():
        old_name = str(row.get('OLD_NAME') or '').strip()
        new_name = str(row.get('NEW_NAME') or '').strip()
        eff = str(row.get('EFFECTIVE_DATE') or '').strip()
        symbol_hint = str(row.get('SYMBOL') or '').strip().upper()
        isin_val = str(row.get('ISIN') or '').strip().upper()
        res = map_row(row, by_symbol, by_isin, by_name)
        if res:
            if res['notes'] == 'name_fuzzy' and not (symbol_hint or isin_val or eff):
                cur.execute(
                    """
                    INSERT INTO name_changes_raw (old_name,new_name,change_date,source,extras,mapped,skip_reason)
                    VALUES (?,?,?,?,?,0,?)
                    """,
                    (old_name,new_name,eff,'excel',f"symbol_hint={symbol_hint};isin={isin_val}", 'weak_evidence')
                )
                skipped += 1
                continue
            cur.execute(
                """
                INSERT INTO alias_events (old_symbol, new_symbol, old_name, new_name, old_isin, new_isin, effective_date, source, confidence, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    None,
                    res['new_symbol'],
                    old_name,
                    (res.get('company_name') or new_name),
                    None,
                    res['new_isin'],
                    eff,
                    'excel',
                    float(res['confidence']),
                    res['notes']
                )
            )
            cur.execute(
                """
                INSERT INTO name_changes_raw (old_name,new_name,change_date,source,extras,mapped,skip_reason)
                VALUES (?,?,?,?,?,1,NULL)
                """,
                (old_name,new_name,eff,'excel',f"symbol_hint={symbol_hint};isin={isin_val}")
            )
            mapped += 1
            if len(samples)<10:
                samples.append({'old_name':old_name,'new_symbol':res['new_symbol'],'confidence':res['confidence']})
        else:
            cur.execute(
                """
                INSERT INTO name_changes_raw (old_name,new_name,change_date,source,extras,mapped,skip_reason)
                VALUES (?,?,?,?,?,0,?)
                """,
                (old_name,new_name,eff,'excel',f"symbol_hint={symbol_hint};isin={isin_val}", 'no_match')
            )
            skipped += 1
    conn.commit()
    print({'mapped':mapped,'skipped':skipped,'samples':samples})
    conn.close()
    return 0

if __name__=='__main__':
    raise SystemExit(main())