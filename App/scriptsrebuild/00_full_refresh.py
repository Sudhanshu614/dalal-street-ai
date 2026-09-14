import subprocess
import sys
import json
from pathlib import Path

def run(cmd):
    print(f"[RUN] {cmd}")
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr)
    return p.returncode

def main():
    root = Path('App/Database').resolve()
    db = root / 'stock_market_new.db'
    excel = root / 'Company_Name_Changes_NSE.xlsx'
    cf = None
    cfs = sorted(root.glob('CF-CA-*.csv'))
    if cfs:
        cf = cfs[-1]
    print(f"[INFO] DB: {db}")
    print(f"[INFO] Excel: {excel}")
    print(f"[INFO] CF-CA: {cf}")

    # Step 1: Excel ingestion
    rc = run(f"python App/scriptsrebuild/05_ingest_name_changes_excel.py")
    if rc != 0:
        print("[WARN] Excel ingestion returned non-zero code")

    # Step 2: Bhavcopy update (includes ISIN mapping and correlation)
    rc = run(
        "python -c \"from App.src.data_fetcher.bhavcopy_downloader import download_latest_bhavcopy;\n"
        "from pathlib import Path; import json;\n"
        f"res=download_latest_bhavcopy(str(Path('{db}').resolve()));\n"
        "print(json.dumps(res, ensure_ascii=False))\""
    )
    if rc != 0:
        print("[WARN] Bhavcopy update returned non-zero code")

    # Step 3: Resolver smoke tests
    rc = run(
        "python -c \"from App.src.data_fetcher.ticker_resolver import TickerResolver;\n"
        "from pathlib import Path; import json;\n"
        f"DB=str(Path('{db}').resolve());\n"
        f"CSV=str(Path('{cf}' if cf else '').resolve()) if '{cf}' else None;\n"
        "r=TickerResolver(DB, CSV);\n"
        "print('ZOMATO:', json.dumps(r.resolve('ZOMATO'), ensure_ascii=False));\n"
        "print('TATAMOTORS:', json.dumps(r.resolve('TATAMOTORS'), ensure_ascii=False))\""
    )
    if rc != 0:
        print("[WARN] Resolver tests returned non-zero code")

if __name__ == '__main__':
    sys.exit(main())