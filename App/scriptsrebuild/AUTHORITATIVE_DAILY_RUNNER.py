import os
import sys
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime
import argparse
import pandas as pd
import json

# App/scriptsrebuild/AUTHORITATIVE_DAILY_RUNNER.py -> scriptsrebuild -> App -> root
BASE = Path(__file__).resolve().parents[2]
DB_FILE = Path(os.getenv("DB_PATH", BASE / "App" / "database" / "stock_market_new.db"))
CSV_DIR = Path(os.getenv("CSV_DIRECTORY", BASE / "App" / "database"))
LOG_DIR = Path(os.getenv("LOG_DIR", BASE / "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG = LOG_DIR / f"AUTHORITATIVE_DAILY_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log(s):
    print(s)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(s + "\n")

def run_cmd(cmd, name):
    r = subprocess.run(cmd, capture_output=True, text=True)
    log(f"[{name}] exit={r.returncode}")
    if r.stdout:
        log(r.stdout.strip())
    if r.stderr:
        log(r.stderr.strip())
    return r.returncode == 0

def _to_ymd(s):
    if not s:
        return None
    s1 = str(s).strip()
    for fmt in ["%Y-%m-%d","%d-%m-%Y","%d/%m/%Y","%d-%b-%Y","%d-%B-%Y"]:
        try:
            return datetime.strptime(s1, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return None

def _discover_one(patterns):
    for pat in patterns:
        files = sorted(CSV_DIR.glob(pat))
        if files:
            return files[-1]
    return None

    

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    date_str = _to_ymd(args.date)
    if not date_str:
        log("[ERROR] invalid date")
        sys.exit(1)
    if not DB_FILE.exists():
        log("[ERROR] database not found")
        sys.exit(1)
    bkp = DB_FILE.parent / f"stock_market_backup_{datetime.now().strftime('%Y%m%d')}.db"
    try:
        import shutil
        shutil.copy2(DB_FILE, bkp)
        log(f"Backup: {bkp}")
    except Exception:
        pass
    ok_all = True
    sm_csv = _discover_one(["stock_master.csv"]) 
    if sm_csv:
        ok_all &= run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "06_ingest_stock_master_csv.py")], "stock_master")
    alias_ok = run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "08_map_excel_alias_events.py")], "alias_map")
    alias_ok &= run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "07_snapshot_and_alias_events.py")], "alias_snapshot")
    ok_all &= alias_ok
    name_xls = _discover_one(["company-name-changes.xlsx","company-name-changes.xls"]) 
    if name_xls:
        ok_all &= run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "05_ingest_name_changes_excel.py"), "--excel", str(name_xls)], "name_changes")
    ok_all &= run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "12_build_company_names_canonical.py")], "names_build")
    ok_all &= run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "14_mismatch_monitor_alias_vs_canonical.py")], "names_mismatch")
    ok_all &= run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "16_sync_company_names_all_tables.py")], "names_sync")
    ok_all &= run_cmd([
        sys.executable,
        "-c",
        (
            "import sys; sys.path.append(sys.argv[1]); "
            "from src.data_fetcher.bhavcopy_downloader import download_latest_bhavcopy; "
            "download_latest_bhavcopy(sys.argv[2])"
        ),
        str(BASE / "App"),
        str(DB_FILE),
    ], "daily_ohlc_latest")
    cfca_csv = _discover_one(["CF-CA-equities-*.csv","CF-CA-*.csv"]) 
    if cfca_csv:
        log(f"[corporate_actions] CSV detected: {cfca_csv.name}")
    ipo_ok = run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "11_import_ipo_data.py")], "ipo_import")
    ok_all &= ipo_ok
    ok_all &= run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "09_calculate_returns.py")], "returns")
    ok_all &= run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "06_rescrape_failed_stocks.py"), "--all"], "screener_rescrape_all")
    try:
        conn = sqlite3.connect(str(DB_FILE))
        cur = conn.cursor()
        summary = {}
        try:
            cur.execute("SELECT COUNT(*) FROM stocks_master")
            summary["stocks_master"] = cur.fetchone()[0]
        except Exception:
            summary["stocks_master"] = None
        try:
            cur.execute("SELECT COUNT(*) FROM daily_ohlc WHERE date = ?", (date_str,))
            summary["daily_ohlc_rows_for_date"] = cur.fetchone()[0]
        except Exception:
            summary["daily_ohlc_rows_for_date"] = None
        try:
            cur.execute("SELECT COUNT(*) FROM fundamentals")
            summary["fundamentals_total"] = cur.fetchone()[0]
        except Exception:
            summary["fundamentals_total"] = None
        try:
            cur.execute("SELECT COUNT(*) FROM fundamentals WHERE DATE(last_updated) = DATE('now')")
            summary["fundamentals_updated_today"] = cur.fetchone()[0]
        except Exception:
            summary["fundamentals_updated_today"] = None
        try:
            cur.execute("SELECT COUNT(*) FROM ipo_data")
            summary["ipo_data_total"] = cur.fetchone()[0]
        except Exception:
            summary["ipo_data_total"] = None
        try:
            cur.execute("SELECT MAX(date) FROM market_indices")
            summary["market_indices_last_date"] = cur.fetchone()[0]
        except Exception:
            summary["market_indices_last_date"] = None
        indices_top3_today = []
        try:
            cur.execute("SELECT index_name, COUNT(*) AS c FROM market_indices WHERE date = ? GROUP BY index_name ORDER BY c DESC LIMIT 3", (date_str,))
            for row in cur.fetchall():
                indices_top3_today.append({"index": row[0], "rows": row[1]})
        except Exception:
            indices_top3_today = []
        summary["indices_top3_today"] = indices_top3_today
        try:
            cur.execute("SELECT MAX(date) FROM fii_dii_data")
            summary["fii_dii_last_date"] = cur.fetchone()[0]
        except Exception:
            summary["fii_dii_last_date"] = None
        try:
            cur.execute("SELECT fii_net, dii_net FROM fii_dii_data WHERE date = (SELECT MAX(date) FROM fii_dii_data)")
            r = cur.fetchone()
            summary["fii_dii_last_nets"] = {"fii_net": r[0], "dii_net": r[1]} if r else None
        except Exception:
            summary["fii_dii_last_nets"] = None
        try:
            cur.execute("SELECT COUNT(*) FROM alias_events WHERE DATE(created_at) = DATE('now')")
            summary["alias_events_today_count"] = cur.fetchone()[0]
        except Exception:
            summary["alias_events_today_count"] = None
        alias_events_today_samples = []
        try:
            cur.execute("SELECT old_symbol, new_symbol, old_name, new_name, notes FROM alias_events WHERE DATE(created_at) = DATE('now') ORDER BY id DESC LIMIT 5")
            for row in cur.fetchall():
                alias_events_today_samples.append({"old_symbol": row[0], "new_symbol": row[1], "old_name": row[2], "new_name": row[3], "notes": row[4]})
        except Exception:
            alias_events_today_samples = []
        summary["alias_events_today_samples"] = alias_events_today_samples
        recent_changes = []
        try:
            cur.execute("SELECT table_name, symbol, before_name, after_name, timestamp FROM company_name_backfill_log ORDER BY id DESC LIMIT 5")
            for row in cur.fetchall():
                recent_changes.append({
                    "table": row[0],
                    "symbol": row[1],
                    "from": row[2],
                    "to": row[3],
                    "ts": row[4]
                })
        except Exception:
            recent_changes = []
        summary["recent_name_changes"] = recent_changes
        summary["cf_ca_csv"] = cfca_csv.name if cfca_csv else None
        log("SUMMARY " + str(summary))
        try:
            log("SUMMARY_JSON " + json.dumps(summary, ensure_ascii=False))
        except Exception:
            pass
        conn.close()
    except Exception as e:
        log(f"SUMMARY ERROR {e}")
    log(f"DONE status={ok_all}")
    sys.exit(0 if ok_all else 1)

if __name__ == "__main__":
    main()

