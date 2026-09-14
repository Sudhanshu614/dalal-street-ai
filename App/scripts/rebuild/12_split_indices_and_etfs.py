"""
12_split_indices_and_etfs.py
Split the massive market_indices table into pure indices and ETFs.
"""
import os
import sqlite3
from pathlib import Path

# App/scripts/rebuild/12_split_indices_and_etfs.py -> rebuild -> scripts -> App -> root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = Path(
    os.getenv("DB_PATH", PROJECT_ROOT / "App" / "database" / "stock_market_new.db")
)

def create_etf_table(conn):
    cursor = conn.cursor()
    # Create market_etfs with same schema as market_indices
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS market_etfs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_name TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            points_change REAL,
            change_percent REAL,
            volume REAL,
            turnover REAL,
            pe_ratio REAL,
            pb_ratio REAL,
            div_yield REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(index_name, date)
        )
    ''')
    # Indexes for performance
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_etf_name ON market_etfs(index_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_etf_date ON market_etfs(date)')
    conn.commit()

def split_data(conn):
    cursor = conn.cursor()
    # Insert ETFs (index_name ending with '-EQ') into market_etfs
    cursor.execute('''
        INSERT OR IGNORE INTO market_etfs
        SELECT * FROM market_indices WHERE index_name LIKE '%-EQ'
    ''')
    conn.commit()
    # Delete those rows from market_indices
    cursor.execute('DELETE FROM market_indices WHERE index_name LIKE "%-EQ"')
    conn.commit()

def report_counts(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM market_indices')
    indices_cnt = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM market_etfs')
    etfs_cnt = cursor.fetchone()[0]
    print(f"Pure Indices rows: {indices_cnt:,}")
    print(f"ETF rows: {etfs_cnt:,}")

def main():
    print("="*80)
    print("Splitting market_indices into pure indices and ETFs")
    print("="*80)
    conn = sqlite3.connect(DB_PATH)
    create_etf_table(conn)
    split_data(conn)
    report_counts(conn)
    conn.close()
    print("Done.")

if __name__ == "__main__":
    main()
