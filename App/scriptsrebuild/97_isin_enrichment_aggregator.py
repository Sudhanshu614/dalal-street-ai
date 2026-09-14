import sqlite3
import json
from pathlib import Path
import pandas as pd
from datetime import datetime

try:
    from nselib import capital_market
    NSELIB_AVAILABLE = True
except Exception:
    NSELIB_AVAILABLE = False

try:
    from jugaad_data.nse import NSELive
    JUGAAD_AVAILABLE = True
except Exception:
    JUGAAD_AVAILABLE = False

def norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    def _n(s):
        s = str(s).strip().lower()
        if 'symbol' in s or 'tckrsymb' in s:
            return 'SYMBOL'
        if 'isin' in s or 'isin number' in s:
            return 'ISIN'
        return s
    return df.rename(columns={c: _n(c) for c in df.columns})

def load_alias_symbols(conn):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT nse_symbol FROM stock_aliases WHERE nse_symbol IS NOT NULL")
    return {str(r[0]).strip().upper() for r in cur.fetchall()}

def load_present_symbols_from_cache(cache_dir: Path):
    files = sorted(cache_dir.glob('bhavcopy_*.csv'))
    if not files:
        return set(), None
    latest = files[-1]
    df = pd.read_csv(latest)
    df.columns = [c.strip() for c in df.columns]
    sym_col = None
    for c in df.columns:
        if c.strip().upper() == 'SYMBOL':
            sym_col = c
            break
    syms = set()
    if sym_col:
        syms = {str(s).strip().upper() for s in df[sym_col].dropna().unique()}
    return syms, latest

def enrich_isin_nselib(conn, target_syms):
    updated = 0
    samples = []
    if not NSELIB_AVAILABLE:
        return updated, samples, False
    try:
        eq = capital_market.equity_list()
        if isinstance(eq, pd.DataFrame) and not eq.empty:
            eq = norm_cols(eq)
            if 'SYMBOL' in eq.columns and 'ISIN' in eq.columns:
                cur = conn.cursor()
                for _, row in eq.iterrows():
                    sym = str(row.get('SYMBOL') or '').strip().upper()
                    isin = str(row.get('ISIN') or '').strip().upper()
                    if sym in target_syms and isin:
                        try:
                            cur.execute("UPDATE stocks_master SET isin = COALESCE(isin, ?) WHERE symbol = ?", (isin, sym))
                            if cur.rowcount:
                                updated += 1
                                if len(samples) < 10:
                                    samples.append({"symbol": sym, "isin": isin})
                        except Exception:
                            pass
                conn.commit()
                return updated, samples, True
    except Exception:
        pass
    return updated, samples, False

def enrich_isin_jugaad(conn, target_syms, limit=100):
    updated = 0
    samples = []
    if not JUGAAD_AVAILABLE:
        return updated, samples, False
    nl = NSELive()
    cur = conn.cursor()
    count = 0
    for sym in list(target_syms)[:limit]:
        try:
            q = nl.stock_quote(sym)
            isin = None
            if isinstance(q, dict):
                isin = (q.get('info') or {}).get('isin') or (q.get('metadata') or {}).get('isin')
            if isin:
                cur.execute("UPDATE stocks_master SET isin = COALESCE(isin, ?) WHERE symbol = ?", (str(isin).strip().upper(), sym))
                if cur.rowcount:
                    updated += 1
                    if len(samples) < 10:
                        samples.append({"symbol": sym, "isin": str(isin).strip().upper()})
            count += 1
        except Exception:
            pass
    conn.commit()
    return updated, samples, True

def main():
    db = Path('App/database/stock_market_new.db').resolve()
    cache_dir = Path('cache/bhavcopy').resolve()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    alias_syms = load_alias_symbols(conn)
    present_syms, latest_file = load_present_symbols_from_cache(cache_dir)
    target_syms = alias_syms | present_syms

    n_upd, n_samples, n_used = enrich_isin_nselib(conn, target_syms)
    j_upd, j_samples, j_used = (0, [], False)
    if n_upd == 0:
        j_upd, j_samples, j_used = enrich_isin_jugaad(conn, target_syms)

    result = {
        "db": str(db),
        "cache_file": str(latest_file) if latest_file else None,
        "alias_symbols": len(alias_syms),
        "present_symbols": len(present_syms),
        "isin_updates_nselib": n_upd,
        "isin_updates_jugaad": j_upd,
        "samples_nselib": n_samples,
        "samples_jugaad": j_samples,
        "source_used": "nselib" if n_used else ("jugaad" if j_used else "none")
    }
    print(json.dumps(result, ensure_ascii=False))
    conn.close()

if __name__ == '__main__':
    main()