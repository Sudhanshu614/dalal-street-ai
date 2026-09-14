import sqlite3
from pathlib import Path
from datetime import datetime
import csv
import re

try:
    from App.config import CSV_DIRECTORY, DB_PATH
except Exception:
    CSV_DIRECTORY = str(Path(__file__).parent.parent.parent / "App" / "database")
    DB_PATH = str(Path(__file__).parent.parent.parent / "App" / "database" / "stock_market_new.db")

DB_FILE = Path(DB_PATH)
CSV_DIR = Path(CSV_DIRECTORY)

def parse_float(text):
    if text is None:
        return None
    s = str(text).strip().replace(',', '')
    if s in {"", "-"}:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", s)
    if not m:
        return None
    try:
        return float(m.group())
    except Exception:
        return None

def parse_date(date_str):
    if not date_str:
        return None
    s = str(date_str).strip()
    if s in {"", "-"}:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%b %d, %Y", "%d-%b-%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return None

def ensure_table(conn):
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ipo_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            company_name TEXT NOT NULL,
            security_type TEXT,
            issue_price REAL,
            issue_start_date TEXT,
            issue_end_date TEXT,
            price_range TEXT,
            listing_date TEXT,
            listing_day_close REAL,
            listing_gains_percent REAL,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(company_name, listing_date)
        )
        """
    )
    cur.execute('CREATE INDEX IF NOT EXISTS idx_ipo_symbol ON ipo_data(symbol)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_ipo_listing_date ON ipo_data(listing_date)')
    conn.commit()

def import_csv(conn):
    if not CSV_DIR.exists():
        print(f"[ERROR] CSV directory not found: {CSV_DIR}")
        return False
    csv_files = sorted(CSV_DIR.glob("IPO-PastIssue-*.csv"))
    if not csv_files:
        print(f"[ERROR] No CSV files found in {CSV_DIR}")
        return False
    cur = conn.cursor()
    total = 0
    for csv_file in csv_files:
        print(f"[INFO] {csv_file.name}")
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                company = (row.get("COMPANY NAME") or "").strip()
                if not company:
                    continue
                symbol = (row.get("Symbol") or "").strip().upper() or None
                security_type = (row.get("SECURITY TYPE") or "").strip().upper() or None
                issue_price = parse_float(row.get("ISSUE PRICE"))
                issue_start_date = parse_date(row.get("ISSUE START DATE"))
                issue_end_date = parse_date(row.get("ISSUE END DATE"))
                price_range = (row.get("PRICE RANGE") or "").strip() or None
                listing_date = parse_date(row.get("DATE OF LISTING"))
                cur.execute(
                    """
                    INSERT INTO ipo_data (
                        symbol, company_name, security_type, issue_price,
                        issue_start_date, issue_end_date, price_range, listing_date,
                        last_updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_name, listing_date) DO UPDATE SET
                        symbol=excluded.symbol,
                        security_type=excluded.security_type,
                        issue_price=excluded.issue_price,
                        issue_start_date=excluded.issue_start_date,
                        issue_end_date=excluded.issue_end_date,
                        price_range=excluded.price_range,
                        last_updated=excluded.last_updated
                    """,
                    (
                        symbol,
                        company,
                        security_type,
                        issue_price,
                        issue_start_date,
                        issue_end_date,
                        price_range,
                        listing_date,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    )
                )
                total += 1
        conn.commit()
    print(f"[SUMMARY] Rows processed: {total}")
    return True

def reset_table(conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM ipo_data")
    conn.commit()

if __name__ == "__main__":
    try:
        if not DB_FILE.exists():
            print("[ERROR] Database not found")
            raise SystemExit(1)
        conn = sqlite3.connect(str(DB_FILE))
        try:
            ensure_table(conn)
            reset_table(conn)
            ok = import_csv(conn)
        finally:
            conn.close()
        raise SystemExit(0 if ok else 1)
    except Exception as e:
        print(f"[ERROR] {e}")
        raise SystemExit(1)
