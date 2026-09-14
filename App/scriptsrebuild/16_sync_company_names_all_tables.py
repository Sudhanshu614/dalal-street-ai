import sqlite3
from pathlib import Path

def table_has_columns(cur, table, cols):
    cur.execute(f"PRAGMA table_info({table})")
    names = { (r[1] if not isinstance(r, sqlite3.Row) else r['name']).lower() for r in cur.fetchall() }
    return all(c.lower() in names for c in cols)

def sync_table(conn, table):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = { (r[1] if not isinstance(r, sqlite3.Row) else r['name']).lower() for r in cur.fetchall() }
    if 'symbol' not in cols or 'company_name' not in cols:
        return {'updated': 0}
    cur.execute(
        f"""
        SELECT t.symbol, t.company_name, c.company_name
        FROM {table} t
        JOIN company_names_canonical c ON UPPER(t.symbol) = UPPER(c.symbol)
        WHERE (t.company_name IS NULL OR TRIM(t.company_name) <> TRIM(c.company_name))
        """
    )
    rows = cur.fetchall()
    updated = 0
    samples = []
    for r in rows:
        sym = r[0]
        before = r[1]
        after = r[2]
        if not sym or not after:
            continue
        has_listing = 'listing_date' in cols
        has_id = 'id' in cols
        if has_listing:
            cond = 't2.id != t1.id' if has_id else 't2.rowid != t1.rowid'
            sql = f"""
                UPDATE {table} AS t1
                SET company_name = ?
                WHERE symbol = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM {table} t2
                      WHERE t2.listing_date = t1.listing_date
                        AND TRIM(t2.company_name) = TRIM(?)
                        AND {cond}
                  )
            """
            cur.execute(sql, (after, sym, after))
        else:
            cur.execute(f"UPDATE {table} SET company_name = ? WHERE symbol = ?", (after, sym))
        cur.execute(
            """
            INSERT INTO company_name_backfill_log (table_name, row_key, symbol, before_name, after_name, source, confidence)
            VALUES (?, ?, ?, ?, ?, 'canonical_sync', 1.0)
            """,
            (table, str(sym), str(sym), before, after)
        )
        updated += cur.rowcount if hasattr(cur, 'rowcount') else 1
        if len(samples) < 10:
            samples.append({'table': table, 'symbol': sym, 'from': before, 'to': after})
    conn.commit()
    return {'updated': updated, 'samples': samples}

def sync_alias_events(conn):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.id, a.new_symbol, a.new_name, c.company_name
        FROM alias_events a
        JOIN company_names_canonical c ON UPPER(a.new_symbol) = UPPER(c.symbol)
        WHERE a.new_symbol IS NOT NULL AND a.new_name IS NOT NULL AND TRIM(a.new_name) <> TRIM(c.company_name)
        """
    )
    rows = cur.fetchall()
    updated = 0
    samples = []
    for r in rows:
        aid = r[0]
        ns = r[1]
        before = r[2]
        after = r[3]
        if not ns or not after:
            continue
        cur.execute("UPDATE alias_events SET new_name = ? WHERE id = ?", (after, aid))
        updated += 1
        if len(samples) < 10:
            samples.append({'id': aid, 'symbol': ns, 'from': before, 'to': after})
    conn.commit()
    return {'updated': updated, 'samples': samples}

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

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [ (r['name'] if isinstance(r, sqlite3.Row) else r[0]) for r in cur.fetchall() ]

    totals = {}
    for t in tables:
        if t in ('daily_ohlc','quarterly_results','annual_financials','market_indices','stock_aliases','alias_events','metadata','sqlite_sequence','download_log','bulk_deals','block_deals','india_vix','fii_dii_data'):
            continue
        if table_has_columns(cur, t, ['symbol','company_name']):
            res = sync_table(conn, t)
            totals[t] = res['updated']

    alias_res = sync_alias_events(conn)
    out = {'tables_updated': totals, 'alias_events_updated': alias_res['updated'], 'alias_samples': alias_res['samples']}
    print(out)
    conn.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())