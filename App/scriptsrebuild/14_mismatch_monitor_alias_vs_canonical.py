import sqlite3
from pathlib import Path

def main():
    db = Path('App/database/stock_market_new.db').resolve()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT symbol, company_name FROM company_names_canonical")
    canonical = { (r['symbol'] if isinstance(r, sqlite3.Row) else r[0]).upper(): (r['company_name'] if isinstance(r, sqlite3.Row) else r[1]) for r in cur.fetchall() }

    cur.execute("SELECT id, new_symbol, new_name, confidence FROM alias_events WHERE new_symbol IS NOT NULL")
    rows = cur.fetchall()
    fixes = 0
    flagged = []
    for r in rows:
        aid = r['id'] if isinstance(r, sqlite3.Row) else r[0]
        ns = (r['new_symbol'] if isinstance(r, sqlite3.Row) else r[1]) or ''
        an = r['new_name'] if isinstance(r, sqlite3.Row) else r[2]
        conf = r['confidence'] if isinstance(r, sqlite3.Row) else r[3]
        key = ns.upper()
        cname = canonical.get(key)
        if key and cname and cname and cname.strip() and (an or '').strip() and cname.strip() != an.strip():
            try:
                cur.execute("UPDATE alias_events SET new_name = ? WHERE id = ?", (cname, aid))
                fixes += 1
                if len(flagged) < 10:
                    flagged.append({'id': aid, 'symbol': key, 'from': an, 'to': cname, 'confidence': conf})
            except Exception:
                pass
    conn.commit()
    print({'alias_events_updated': fixes, 'samples': flagged})
    conn.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())