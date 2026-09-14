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

    cur.execute(
        """
        SELECT f.symbol AS symbol, f.company_name AS before_name, c.company_name AS after_name
        FROM fundamentals f
        JOIN company_names_canonical c ON UPPER(f.symbol) = UPPER(c.symbol)
        WHERE (f.company_name IS NULL OR TRIM(f.company_name) <> TRIM(c.company_name))
        """
    )
    rows = cur.fetchall()
    updated = 0
    samples = []
    for r in rows:
        sym = r['symbol'] if isinstance(r, sqlite3.Row) else r[0]
        before = r['before_name'] if isinstance(r, sqlite3.Row) else r[1]
        after = r['after_name'] if isinstance(r, sqlite3.Row) else r[2]
        if not sym or not after:
            continue
        cur.execute("UPDATE fundamentals SET company_name = ? WHERE symbol = ?", (after, sym))
        cur.execute(
            """
            INSERT INTO company_name_backfill_log (table_name, row_key, symbol, before_name, after_name, source, confidence)
            VALUES ('fundamentals', ?, ?, ?, ?, 'canonical_sync', 1.0)
            """,
            (str(sym), str(sym), before, after)
        )
        updated += 1
        if len(samples) < 10:
            samples.append({'symbol': sym, 'from': before, 'to': after})

    conn.commit()
    print({'updated': updated, 'samples': samples})
    conn.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())