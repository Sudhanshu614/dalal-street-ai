import sqlite3
from pathlib import Path

def main():
    db = Path('App/database/stock_market_new.db').resolve()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS company_name_backfill_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT,
            row_key TEXT,
            symbol TEXT,
            before_name TEXT,
            after_name TEXT,
            source TEXT,
            confidence REAL,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()

    def backfill_table(table: str, key_field: str = 'symbol'):
        try:
            cur.execute(f"PRAGMA table_info({table})")
            cols = [r['name'] if isinstance(r, sqlite3.Row) else r[1] for r in cur.fetchall()]
            if key_field not in cols:
                return {'updated': 0}
            has_company = ('company_name' in cols)
            # If table lacks company_name column, skip persisted update
            if not has_company:
                return {'updated': 0}
            cur.execute(f"SELECT {key_field}, company_name FROM {table}")
            rows = cur.fetchall()
            updated = 0
            for r in rows:
                sym = r[0] if not isinstance(r, sqlite3.Row) else r[key_field]
                before = r[1] if not isinstance(r, sqlite3.Row) else r['company_name']
                if before:
                    continue
                if not sym:
                    continue
                cur.execute("SELECT company_name, source, confidence FROM company_names_canonical WHERE symbol = ?", (str(sym).upper(),))
                m = cur.fetchone()
                if not m:
                    continue
                after = m['company_name'] if isinstance(m, sqlite3.Row) else m[0]
                src = m['source'] if isinstance(m, sqlite3.Row) else m[1]
                conf = m['confidence'] if isinstance(m, sqlite3.Row) else m[2]
                cur.execute(f"UPDATE {table} SET company_name = ? WHERE {key_field} = ?", (after, sym))
                cur.execute(
                    """
                    INSERT INTO company_name_backfill_log (table_name, row_key, symbol, before_name, after_name, source, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (table, str(sym), str(sym), before, after, src, conf)
                )
                updated += 1
            conn.commit()
            return {'updated': updated}
        except Exception:
            return {'updated': 0}

    res_f = backfill_table('fundamentals')
    res_ca = backfill_table('corporate_actions')
    print({'fundamentals_updated': res_f['updated'], 'corporate_actions_updated': res_ca['updated']})
    conn.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())