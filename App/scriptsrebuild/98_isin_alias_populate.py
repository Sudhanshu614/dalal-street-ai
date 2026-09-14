import sqlite3
from pathlib import Path
import json
import pandas as pd

try:
    from nselib import capital_market
    NSELIB_AVAILABLE = True
except Exception:
    NSELIB_AVAILABLE = False

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    def _norm(s):
        s = str(s).strip().lower()
        if 'symbol' in s:
            return 'SYMBOL'
        if 'isin' in s:
            return 'ISIN'
        return s
    return df.rename(columns={c: _norm(c) for c in df.columns})

def main():
    db = Path('App/database/stock_market_new.db').resolve()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT nse_symbol FROM stock_aliases WHERE nse_symbol IS NOT NULL")
    alias_symbols = {str(r['nse_symbol']).strip().upper() for r in cur.fetchall()}

    isin_map = {}
    source = "none"

    # Try NSELIB equity list first (since bhavcopy may not have ISIN)
    if NSELIB_AVAILABLE:
        try:
            eq_df = capital_market.equity_list()
            if isinstance(eq_df, pd.DataFrame) and not eq_df.empty:
                eq_df = normalize_cols(eq_df)
                if 'SYMBOL' in eq_df.columns and 'ISIN' in eq_df.columns:
                    for _, row in eq_df.iterrows():
                        sym = str(row.get('SYMBOL') or '').strip().upper()
                        isin = str(row.get('ISIN') or '').strip().upper()
                        if sym and isin:
                            isin_map[sym] = isin
                    source = "nselib_equity_list"
        except Exception:
            pass

    updated = 0
    samples = []
    for sym in sorted(alias_symbols):
        isin = isin_map.get(sym)
        if not isin:
            continue
        try:
            cur.execute("UPDATE stocks_master SET isin = COALESCE(isin, ?) WHERE symbol = ?", (isin, sym))
            if cur.rowcount:
                updated += 1
                if len(samples) < 10:
                    samples.append({"symbol": sym, "isin": isin})
        except Exception:
            pass
    conn.commit()
    conn.close()

    print(json.dumps({
        "db": str(db),
        "alias_symbols": len(alias_symbols),
        "isin_updates": updated,
        "samples": samples,
        "source": source
    }, ensure_ascii=False))

if __name__ == '__main__':
    main()