import sqlite3
import json
from pathlib import Path

def main():
    db = Path('App/database/stock_market_new.db').resolve()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    out = {}
    cur.execute("SELECT symbol,is_active,company_name,isin FROM stocks_master WHERE symbol IN ('ETERNAL','TMPV','TATAMOTORS')")
    out['stocks_master'] = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT old_name,new_name,nse_symbol,change_date,confidence FROM stock_aliases WHERE old_name LIKE '%ZOMATO%' OR new_name LIKE '%ZOMATO%' OR new_name LIKE '%ETERNAL%'")
    out['aliases_zomato'] = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT old_name,new_name,nse_symbol,change_date,confidence FROM stock_aliases WHERE nse_symbol='TMPV' OR old_name LIKE '%TATA%' OR new_name LIKE '%TATA%'")
    out['aliases_tata'] = [dict(r) for r in cur.fetchall()][:20]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    conn.close()

if __name__ == '__main__':
    main()