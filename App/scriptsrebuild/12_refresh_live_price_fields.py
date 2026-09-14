import sys
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta
import argparse
import time
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[2]))
from App.config import config

def _conn():
    return sqlite3.connect(str(config.DB_PATH))

def _pos(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except Exception:
        return None

def _latest_trade_date(cur):
    d = cur.execute("SELECT MAX(date) FROM daily_ohlc").fetchone()[0]
    return d

def _build_eod_maps(cur, latest_date):
    cur.execute("SELECT symbol, close FROM daily_ohlc WHERE date = ?", (latest_date,))
    current_price_map = {sym: _pos(close) for sym, close in cur.fetchall()}
    # 52-week window
    try:
        # SQLite lacks DATE arithmetic on text reliably; compute start in Python
        start_dt = datetime.strptime(latest_date, "%Y-%m-%d") - timedelta(days=365)
        start_str = start_dt.strftime("%Y-%m-%d")
        cur.execute(
            """
            SELECT symbol, MAX(high) AS max_h, MIN(low) AS min_l
            FROM daily_ohlc
            WHERE date >= ? AND date <= ?
            GROUP BY symbol
            """,
            (start_str, latest_date)
        )
        week52_map = {sym: ( _pos(max_h), _pos(min_l)) for sym, max_h, min_l in cur.fetchall()}
    except Exception:
        week52_map = {}
    return current_price_map, week52_map

def _to_ddmmyyyy(s):
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
        return d.strftime("%d-%m-%Y")
    except Exception:
        return s

def _build_nselib_maps(latest_date):
    try:
        from nselib import capital_market
    except Exception:
        return {}, {}, {}
    date_str = _to_ddmmyyyy(latest_date)
    price_map = {}
    pe_map = {}
    week52_map = {}
    try:
        df = capital_market.bhav_copy_with_delivery(date_str)
        if isinstance(df, pd.DataFrame) and not df.empty:
            for _, row in df.iterrows():
                sym = str(row.get('SYMBOL') or '').strip().upper()
                if not sym:
                    continue
                cp = _pos(row.get('CLOSE_PRICE') or row.get('LAST_PRICE'))
                if cp is not None:
                    price_map[sym] = cp
    except Exception:
        pass
    try:
        dfw = capital_market.week_52_high_low_report(date_str)
        if isinstance(dfw, pd.DataFrame) and not dfw.empty:
            for _, row in dfw.iterrows():
                sym = str(row.get('SYMBOL') or '').strip().upper()
                if not sym:
                    continue
                hi = _pos(row.get('HIGH_52_WEEK'))
                lo = _pos(row.get('LOW_52_WEEK'))
                week52_map[sym] = (hi, lo)
    except Exception:
        pass
    try:
        df2 = capital_market.pe_ratio(date_str)
        if isinstance(df2, pd.DataFrame) and not df2.empty:
            cols = {c: c for c in df2.columns}
            def _pe_col():
                for cand in ['ADJUSTEDP/E','SYMBOLP/E','P/E','PE','P E']:
                    if cand in df2.columns:
                        return cand
                # fallback: pick any col containing 'P' and 'E'
                for c in df2.columns:
                    lc = str(c).upper()
                    if 'P' in lc and 'E' in lc:
                        return c
                return None
            pe_col = _pe_col()
            for _, row in df2.iterrows():
                sym = str(row.get('SYMBOL') or '').strip().upper()
                raw = row.get(pe_col) if pe_col else None
                val = _pos(raw)
                if sym and val is not None:
                    pe_map[sym] = val
    except Exception:
        pass
    return price_map, week52_map, pe_map

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--date", type=str, default=None)
    ap.add_argument("--source", type=str, default="ohlc", choices=["ohlc","nselib"]) 
    args = ap.parse_args()

    conn = _conn()
    cur = conn.cursor()
    latest_date = args.date or _latest_trade_date(cur)
    if not latest_date:
        print("[ERROR] No daily_ohlc data available")
        return
    if args.source == "nselib":
        cp_map, w52_map_nse, pe_map = _build_nselib_maps(latest_date)
        cp_eod_map, w52_map_eod = _build_eod_maps(cur, latest_date)
    else:
        cp_map, w52_map_eod = _build_eod_maps(cur, latest_date)
        w52_map_nse = {}
        cp_eod_map = {}
        pe_map = {}

    base_sql = "SELECT symbol, book_value, current_price, week52_high, week52_low, pe_ratio, eps FROM fundamentals"
    if args.only_missing:
        base_sql += " WHERE current_price IS NULL OR week52_high IS NULL OR week52_low IS NULL OR pe_ratio IS NULL"
    if args.limit and args.limit > 0:
        base_sql += f" LIMIT {int(args.limit)}"
    cur.execute(base_sql)
    rows = cur.fetchall()
    total = len(rows)
    ok = 0
    fail = 0
    start = time.time()
    for idx, (sym, book_v, cur_price, cur_high, cur_low, cur_pe, eps_v) in enumerate(rows, 1):
        price = cp_map.get(sym)
        if not price and cp_eod_map:
            price = cp_eod_map.get(sym)
        wh_e, wl_e = w52_map_eod.get(sym, (None, None))
        wh_n, wl_n = w52_map_nse.get(sym, (None, None))
        wh = wh_e if wh_e is not None else wh_n
        wl = wl_e if wl_e is not None else wl_n
        pe = pe_map.get(sym)
        if pe is None and price is not None and eps_v is not None:
            try:
                epsf = float(eps_v)
                if epsf > 0:
                    pe = _pos(float(price) / epsf)
            except Exception:
                pass
        if price is None and wh is None and wl is None:
            fail += 1
            if args.progress:
                print(f"[FAIL] {sym}")
            continue
        pb = None
        try:
            if book_v is not None and price is not None:
                bv = float(book_v)
                if bv > 0:
                    pb = float(price) / bv
        except Exception:
            pb = None
        if args.dry_run:
            ok += 1
            if args.progress:
                print(f"[OK] {sym} price={price} high={wh} low={wl} pe={pe} pb={pb}")
            continue
        cur.execute(
            """
            UPDATE fundamentals
            SET current_price = COALESCE(?, current_price),
                week52_high = COALESCE(?, week52_high),
                week52_low = COALESCE(?, week52_low),
                pe_ratio = COALESCE(?, pe_ratio),
                pb_ratio = COALESCE(?, pb_ratio),
                last_updated = CURRENT_TIMESTAMP
            WHERE symbol = ?
            """,
            (price, wh, wl, pe, pb, sym)
        )
        ok += 1
        if args.progress:
            print(f"[OK] {sym} price={price} high={wh} low={wl} pe={pe} pb={pb}")
        if idx % max(1, args.batch) == 0:
            elapsed = time.time() - start
            rate = idx / elapsed if elapsed > 0 else 0
            elapsed_str = f"{elapsed:.1f}s" if elapsed < 60 else f"{elapsed/60:.1f}m"
            print(f"[PROGRESS] {idx}/{total} ok={ok} fail={fail} rate={rate:.2f} sym/s elapsed={elapsed_str}")
    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"[LIVE REFRESH] total={total} ok={ok} fail={fail} dry_run={args.dry_run} at {datetime.now().isoformat(timespec='seconds')}")

if __name__ == "__main__":
    main()
