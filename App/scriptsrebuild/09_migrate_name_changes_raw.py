import sqlite3
from pathlib import Path

def main():
    db = Path('App/database/stock_market_new.db').resolve()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(name_changes_raw)")
    cols = [r['name'] if isinstance(r, sqlite3.Row) else r[1] for r in cur.fetchall()]
    if 'mapped' not in cols:
        cur.execute("ALTER TABLE name_changes_raw ADD COLUMN mapped INTEGER DEFAULT 0")
    if 'skip_reason' not in cols:
        cur.execute("ALTER TABLE name_changes_raw ADD COLUMN skip_reason TEXT")
    conn.commit()
    cur.execute("PRAGMA table_info(name_changes_raw)")
    cols2 = [r['name'] if isinstance(r, sqlite3.Row) else r[1] for r in cur.fetchall()]
    print({'db': str(db), 'name_changes_raw_columns': cols2})
    conn.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())