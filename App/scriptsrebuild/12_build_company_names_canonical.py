import sqlite3
from pathlib import Path

def main():
    db = Path('App/database/stock_market_new.db').resolve()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS company_names_canonical (
            symbol TEXT PRIMARY KEY,
            company_name TEXT,
            source TEXT,
            confidence REAL,
            effective_date_start TEXT,
            effective_date_end TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()

    cur.execute("DELETE FROM company_names_canonical")

    cur.execute("SELECT symbol, company_name FROM stocks_master")
    rows = cur.fetchall()
    for r in rows:
        sym = (r['symbol'] if isinstance(r, sqlite3.Row) else r[0])
        name = (r['company_name'] if isinstance(r, sqlite3.Row) else r[1])
        if sym:
            cur.execute(
                """
                INSERT OR REPLACE INTO company_names_canonical
                (symbol, company_name, source, confidence, effective_date_start, effective_date_end)
                VALUES (?, ?, 'stocks_master', 1.0, NULL, NULL)
                """,
                (sym.upper(), name)
            )

    cur.execute(
        """
        SELECT new_symbol AS symbol, new_name AS company_name, confidence, effective_date
        FROM alias_events
        WHERE new_symbol IS NOT NULL AND new_name IS NOT NULL
        ORDER BY effective_date DESC, id DESC
        """
    )
    for r in cur.fetchall():
        sym = r['symbol'] if isinstance(r, sqlite3.Row) else r[0]
        name = r['company_name'] if isinstance(r, sqlite3.Row) else r[1]
        conf = r['confidence'] if isinstance(r, sqlite3.Row) else r[2]
        eff = r['effective_date'] if isinstance(r, sqlite3.Row) else r[3]
        if not sym or not name:
            continue
        try:
            # Prefer stocks_master name if already set; only update when missing
            cur.execute("SELECT company_name FROM company_names_canonical WHERE symbol = ?", (sym.upper(),))
            existing = cur.fetchone()
            if existing and (existing[0] and existing[0].strip()):
                # Do not override authoritative name
                pass
            else:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO company_names_canonical
                    (symbol, company_name, source, confidence, effective_date_start, effective_date_end)
                    VALUES (?, ?, 'alias_events', COALESCE(?, 0.9), ?, NULL)
                    """,
                    (sym.upper(), name, float(conf) if conf is not None else None, eff)
                )
        except Exception:
            continue

    conn.commit()
    cur.execute("SELECT COUNT(*) AS cnt FROM company_names_canonical")
    cnt = cur.fetchone()[0]
    print({'canonical_rows': cnt})
    conn.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())