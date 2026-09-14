import os
import sqlite3
from pathlib import Path

# App/scripts/rebuild/cleanup_db_casing.py -> rebuild -> scripts -> App -> root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_FILE = Path(
    os.getenv("DB_PATH", PROJECT_ROOT / "App" / "database" / "stock_market_new.db")
)

def cleanup_table(conn, table_name):
    cursor = conn.cursor()
    print(f"Cleaning up {table_name}...")
    
    # 1. Get all records with non-uppercase names
    cursor.execute(f"SELECT index_name, date, id FROM {table_name} WHERE index_name != UPPER(index_name)")
    rows = cursor.fetchall()
    print(f"  Found {len(rows)} records with mixed casing")
    
    for mixed_name, date, mixed_id in rows:
        upper_name = mixed_name.upper()
        
        # 2. Check if the upper case version exists for the same date
        cursor.execute(f"SELECT id FROM {table_name} WHERE index_name = ? AND date = ?", (upper_name, date))
        dup = cursor.fetchone()
        
        if dup:
            # Duplicate exists, delete the mixed case one
            cursor.execute(f"DELETE FROM {table_name} WHERE id = ?", (mixed_id,))
        else:
            # No duplicate, just update to upper
            cursor.execute(f"UPDATE {table_name} SET index_name = ? WHERE id = ?", (upper_name, mixed_id))
            
    conn.commit()
    print(f"  Cleanup of {table_name} complete")

def main():
    if not DB_FILE.exists():
        print("DB not found")
        return
        
    conn = sqlite3.connect(DB_FILE)
    cleanup_table(conn, 'market_indices')
    cleanup_table(conn, 'market_etfs')
    conn.close()

if __name__ == "__main__":
    main()
