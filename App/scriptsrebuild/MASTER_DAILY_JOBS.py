import sys
import subprocess
import sqlite3
import shutil
import json
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent.parent.parent
DB_FILE = BASE / "App" / "database" / "stock_market_new.db"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG = LOG_DIR / f"MASTER_DAILY_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log(s):
    print(s)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(s + "\n")

def backup_db():
    if DB_FILE.exists():
        dest = DB_FILE.parent / f"stock_market_backup_{datetime.now().strftime('%Y%m%d')}.db"
        shutil.copy2(DB_FILE, dest)
        return str(dest)
    return None

def run_cmd(cmd, name):
    r = subprocess.run(cmd, capture_output=True, text=True)
    log(f"[{name}] exit={r.returncode}")
    if r.stdout:
        log(r.stdout.strip())
    if r.stderr:
        log(r.stderr.strip())
    return r.returncode == 0

def update_daily_ohlc():
    sys.path.append(str(BASE / "App"))
    from src.utils.job_runs import _conn as _jr_conn, ensure_table as _jr_ensure, set_last_run
    from src.data_fetcher.bhavcopy_downloader import download_latest_bhavcopy
    try:
        download_latest_bhavcopy(str(DB_FILE))
        jr = _jr_conn()
        try:
            _jr_ensure(jr)
            set_last_run(jr, "daily_ohlc", "daily_ohlc", datetime.now().strftime('%Y-%m-%d'), "SUCCESS")
        finally:
            jr.close()
        return True
    except Exception as e:
        log(f"[daily_ohlc] {e}")
        return False

def verify_counts(date_str):
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    res = {}
    try:
        cur.execute("SELECT COUNT(*) AS c FROM daily_ohlc WHERE date=?", (date_str,))
        res["daily_ohlc"] = cur.fetchone()[0]
    except Exception:
        cur.execute("SELECT COUNT(*) AS c FROM daily_ohlc")
        res["daily_ohlc"] = cur.fetchone()[0]
    try:
        cur.execute("SELECT COUNT(*) AS c FROM market_indices WHERE date=?", (date_str,))
        res["market_indices"] = cur.fetchone()[0]
    except Exception:
        cur.execute("SELECT COUNT(*) AS c FROM market_indices")
        res["market_indices"] = cur.fetchone()[0]
    try:
        cur.execute("SELECT COUNT(*) AS c FROM fii_dii_data WHERE date=?", (date_str,))
        res["fii_dii_data"] = cur.fetchone()[0]
    except Exception:
        cur.execute("SELECT COUNT(*) AS c FROM fii_dii_data")
        res["fii_dii_data"] = cur.fetchone()[0]
    # bulk_deals skipped
    conn.close()
    return res

def main():
    log("START MASTER DAILY JOBS")
    bkp = backup_db()
    if bkp:
        log(f"Backup: {bkp}")
    today = datetime.now().strftime('%Y-%m-%d')

    ok_ohlc = update_daily_ohlc()
    ok_indices = run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "10_scrape_market_indices.py")], "market_indices")
    ok_fii = run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "14_scrape_fii_dii_data.py")], "fii_dii_data")
    ok_bulk = True

    ok_ca = True
    ok_funda = True
    ok_returns = True
    try:
        ok_returns = run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "09_calculate_returns.py")], "returns")
    except Exception as e:
        log(f"[returns] {e}")
        ok_returns = False
    try:
        ok_funda = run_cmd([sys.executable, str(BASE / "App" / "scriptsrebuild" / "08_scrape_enhanced_fundamentals.py")], "fundamentals_enhanced")
    except Exception as e:
        log(f"[fundamentals_enhanced] {e}")
        ok_funda = False

    summary_counts = verify_counts(today)
    summary = {
        "date": today,
        "jobs": {
            "daily_ohlc": ok_ohlc,
            "market_indices": ok_indices,
            "fii_dii_data": ok_fii,
            # bulk_deals skipped
            "returns": ok_returns,
            "fundamentals_enhanced": ok_funda
        },
        "counts": summary_counts
    }
    log(json.dumps(summary, ensure_ascii=False))
    out = LOG_DIR / f"MASTER_DAILY_{today}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False)
    all_ok = all(summary["jobs"].values())
    log("END MASTER DAILY JOBS")
    sys.exit(0 if all_ok else 1)

if __name__ == "__main__":
    main()
