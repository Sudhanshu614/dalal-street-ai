import sqlite3
import pandas as pd
import re
from pathlib import Path
from datetime import datetime

def norm_cols(df):
    def m(s):
        s=str(s).strip().lower()
        if 'symbol' in s:
            return 'SYMBOL'
        if 'company' in s and 'name' in s:
            return 'COMPANY_NAME'
        if 'series' in s:
            return 'SERIES'
        if 'date' in s and 'list' in s:
            return 'DATE_OF_LISTING'
        if 'face' in s and 'value' in s:
            return 'FACE_VALUE'
        if 'isin' in s:
            return 'ISIN_NUMBER'
        return s
    return df.rename(columns={c:m(c) for c in df.columns})

def main():
    db=Path('App/database/stock_market_new.db').resolve()
    csv_path=Path('App/database/stock_master.csv').resolve()
    df=pd.read_csv(csv_path)
    df=norm_cols(df)
    required=['SYMBOL','COMPANY_NAME']
    for r in required:
        if r not in df.columns:
            print({'error':'missing_columns','missing':r})
            return 1
    conn=sqlite3.connect(str(db))
    conn.row_factory=sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass
    cur=conn.cursor()
    present=set()
    inserts=0
    updates=0
    reactivated=0
    deactivated=0
    samples=[]
    for _,row in df.iterrows():
        sym=str(row.get('SYMBOL') or '').strip().upper()
        if not sym:
            continue
        present.add(sym)
        name=str(row.get('COMPANY_NAME') or '').strip()
        series=str(row.get('SERIES') or 'EQ').strip().upper()
        listing=str(row.get('DATE_OF_LISTING') or '').strip()
        face=row.get('FACE_VALUE')
        isin=str(row.get('ISIN_NUMBER') or '').strip().upper()
        cur.execute('SELECT symbol,is_active,company_name,listing_date,face_value,isin,series FROM stocks_master WHERE symbol=?', (sym,))
        r=cur.fetchone()
        if r is None:
            cur.execute('INSERT OR IGNORE INTO stocks_master(symbol,company_name,listing_date,face_value,isin,series,is_active,updated_at) VALUES(?,?,?,?,?,?,1,CURRENT_TIMESTAMP)', (sym,name,listing,face,isin,series))
            inserts+=cur.rowcount
        else:
            set_is_active=1
            if r['is_active']==0:
                reactivated+=1
            cur.execute('UPDATE stocks_master SET company_name=?,listing_date=?,face_value=?,isin=COALESCE(isin,?),series=?,is_active=?,updated_at=CURRENT_TIMESTAMP WHERE symbol=?', (name,listing,face,isin,series,set_is_active,sym))
            updates+=cur.rowcount
        if len(samples)<10 and isin:
            samples.append({'symbol':sym,'isin':isin})
    cur.execute('SELECT symbol FROM stocks_master')
    all_syms={row['symbol'] for row in cur.fetchall()}
    missing=list(all_syms-present)
    if missing:
        placeholders=','.join('?'*len(missing))
        cur.execute(f'UPDATE stocks_master SET is_active=0, updated_at=CURRENT_TIMESTAMP WHERE symbol IN ({placeholders})', missing)
        deactivated+=cur.rowcount
    conn.commit()
    try:
        cur.execute('INSERT INTO download_log(table_name,symbol,status,records_added,error_message,timestamp) VALUES(?,NULL,?,?,NULL,CURRENT_TIMESTAMP)', ('stocks_master','success',inserts+updates))
        conn.commit()
    except Exception:
        pass
    print({'db':str(db),'inserts':inserts,'updates':updates,'reactivated':reactivated,'deactivated':deactivated,'samples':samples})
    conn.close()
    return 0

if __name__=='__main__':
    raise SystemExit(main())