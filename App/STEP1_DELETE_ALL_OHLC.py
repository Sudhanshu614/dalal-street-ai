"""
STEP 1: Delete All OHLC Data
-----------------------------
Clears the daily_ohlc table to prepare for fresh backfill with EQ+BE series.

WARNING: This deletes ALL historical OHLC data!
"""
import os
import sqlite3
from pathlib import Path

# App/STEP1_DELETE_ALL_OHLC.py -> App -> <repo root>
PROJECT_ROOT = Path(__file__).resolve().parents[1]
db_path = Path(
    os.getenv("DB_PATH", PROJECT_ROOT / "App" / "database" / "stock_market_new.db")
)

print("="*70)
print("DELETE ALL OHLC DATA")
print("="*70)
print("\n[WARNING] This will DELETE all records from daily_ohlc table!")
print("[WARNING] You should have a backup before proceeding!")

response = input("\nType 'DELETE ALL' to confirm: ")
if response != 'DELETE ALL':
    print("[ABORT] Cancelled")
    exit(0)

conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

# Count before
cursor.execute("SELECT COUNT(*) FROM daily_ohlc")
count_before = cursor.fetchone()[0]
print(f"\n[INFO] Records before: {count_before:,}")

# Delete all
print("[INFO] Deleting all records...")
cursor.execute("DELETE FROM daily_ohlc")
conn.commit()

# Count after
cursor.execute("SELECT COUNT(*) FROM daily_ohlc")
count_after = cursor.fetchone()[0]

print(f"[INFO] Records after: {count_after}")
print(f"[SUCCESS] Deleted {count_before:,} records")

conn.close()

print("\n[NEXT STEP] Now run: python BACKFILL_BE_SERIES_CLEAN.py")
print("="*70)
