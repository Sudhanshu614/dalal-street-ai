import os
import sqlite3
import pandas as pd
import difflib
import json
import re
from pathlib import Path
from datetime import datetime

# App/scriptsrebuild/05_ingest_name_changes_excel.py -> scriptsrebuild -> App -> root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(
    os.getenv("DB_PATH", PROJECT_ROOT / "App" / "database" / "stock_market_new.db")
)
CSV_DIRECTORY = Path(
    os.getenv("CSV_DIRECTORY", PROJECT_ROOT / "App" / "database")
)

def _clean_name(x):
    s = str(x or "")
    s = re.sub(r"\s+(Ltd\.?|Limited|Private|Pvt\.?|Corporation|Corp\.?|Inc\.?)$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[&()\[\].,]", " ", s)
    s = " ".join(s.split())
    return s.strip().upper()

def _discover_excel_rows(xlsx_path: Path) -> pd.DataFrame:
    sheets = pd.read_excel(xlsx_path, sheet_name=None)
    frames = []
    for _, df in sheets.items():
        if isinstance(df, pd.DataFrame) and not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c: c for c in df.columns}
    def _map(c):
        lc = c.lower()
        if re.search(r"old.*name|previous.*name", lc):
            return "old_name"
        if re.search(r"new.*name|current.*name", lc):
            return "new_name"
        if re.search(r"date|effective", lc):
            return "change_date"
        if re.search(r"symbol|ticker|nse", lc):
            return "symbol"
        return c
    mapped = {c: _map(c) for c in df.columns}
    df2 = df.rename(columns=mapped)
    core = {}
    for k in ["old_name","new_name","change_date","symbol"]:
        if k in df2.columns:
            core[k] = df2[k]
        else:
            core[k] = None
    core_df = pd.DataFrame(core)
    extras_cols = [c for c in df2.columns if c not in core_df.columns]
    extras = []
    for i in range(len(df2)):
        row = {c: (None if pd.isna(df2.iloc[i][c]) else df2.iloc[i][c]) for c in extras_cols}
        extras.append(json.dumps(row, default=str))
    core_df["extras"] = extras
    return core_df

def _load_nse_map(conn):
    cur = conn.cursor()
    cur.execute("SELECT symbol, company_name, isin FROM stocks_master WHERE is_active = 1")
    rows = cur.fetchall()
    by_name = {}
    for sym, name, isin in rows:
        nm = _clean_name(name)
        if nm:
            by_name[nm] = (sym, name, isin)
    return by_name

def _find_symbol(name, nse_map):
    nm = _clean_name(name)
    if not nm:
        return None, None, 0.0
    if nm in nse_map:
        sym, real_name, _ = nse_map[nm]
        return sym, real_name, 1.0
    names = list(nse_map.keys())
    matches = difflib.get_close_matches(nm, names, n=1, cutoff=0.7)
    if matches:
        m = matches[0]
        sym, real_name, _ = nse_map[m]
        conf = difflib.SequenceMatcher(None, nm, m).ratio()
        return sym, real_name, conf
    return None, None, 0.0

def _ensure_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS name_changes_raw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_name TEXT,
            new_name TEXT,
            change_date TEXT,
            source TEXT,
            extras TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_name TEXT NOT NULL,
            new_name TEXT NOT NULL,
            nse_symbol TEXT,
            change_date TEXT,
            confidence REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    conn.commit()

def _upsert_raw(conn, records):
    cur = conn.cursor()
    count = 0
    for r in records:
        cur.execute(
            """
            INSERT INTO name_changes_raw (old_name,new_name,change_date,source,extras)
            VALUES (?,?,?,?,?)
            """,
            (r.get("old_name"), r.get("new_name"), r.get("change_date"), "excel", r.get("extras"))
        )
        count += 1
    conn.commit()
    return count

def _upsert_aliases(conn, records, nse_map):
    cur = conn.cursor()
    cur.execute("SELECT symbol FROM stocks_master WHERE is_active = 1")
    active_syms = {row[0] for row in cur.fetchall()}
    count = 0
    for r in records:
        sym = r.get("symbol")
        conf = 0.0
        if sym and isinstance(sym, str):
            sym_u = sym.strip().upper()
            if sym_u in active_syms:
                conf = 1.0
            else:
                sym_u = None
        else:
            sym_u = None
        if not sym_u:
            s1, _, c1 = _find_symbol(r.get("new_name"), nse_map)
            if s1:
                sym_u = s1
                conf = c1
            else:
                s2, _, c2 = _find_symbol(r.get("old_name"), nse_map)
                if s2:
                    sym_u = s2
                    conf = c2
        if sym_u:
            cur.execute(
                """
                INSERT INTO stock_aliases (old_name,new_name,nse_symbol,change_date,confidence)
                VALUES (?,?,?,?,?)
                """,
                (r.get("old_name"), r.get("new_name"), sym_u, r.get("change_date"), conf)
            )
            count += 1
    conn.commit()
    return count

def main():
    db_path = DB_PATH
    xlsx = CSV_DIRECTORY / "Company_Name_Changes_NSE.xlsx"
    df = _discover_excel_rows(xlsx)
    if df is None or df.empty:
        print("[ERROR] No Excel data")
        return 1
    core = _normalize(df)
    records = []
    for _, row in core.iterrows():
        records.append({
            "old_name": row.get("old_name"),
            "new_name": row.get("new_name"),
            "change_date": row.get("change_date"),
            "symbol": row.get("symbol"),
            "extras": row.get("extras")
        })
    conn = sqlite3.connect(str(db_path))
    _ensure_tables(conn)
    nse_map = _load_nse_map(conn)
    raw_count = _upsert_raw(conn, records)
    alias_count = _upsert_aliases(conn, records, nse_map)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO download_log (table_name,symbol,status,records_added,error_message,timestamp)
            VALUES (?,NULL,?, ?, NULL, CURRENT_TIMESTAMP)
            """,
            ("stock_aliases", "success", alias_count)
        )
        conn.commit()
    except Exception:
        pass
    conn.close()
    print(f"[OK] Ingested raw={raw_count} aliases={alias_count}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())