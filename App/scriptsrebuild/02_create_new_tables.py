"""
Phase 2: Create New NSE-Authoritative Tables

Creates 4 new tables for pure NSE data:
1. name_change_events - NSE namechange.csv (authoritative)
2. symbol_change_events - NSE symbolchange.csv (authoritative)
3. corporate_events - NSE CF-CA (daily reload)
4. delisting_events - Inferred from IPO gap

All tables indexed for fast queries (<10ms).
"""

import sqlite3

DB_PATH = 'App/database/stock_market_new.db'

def create_tables():
    """Create new NSE-authoritative tables with indexes"""
    print("=" * 60)
    print("PHASE 2: CREATE NEW TABLES")
    print("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Table 1: Name Changes
    print("\n[1/4] Creating name_change_events table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS name_change_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            old_name TEXT NOT NULL,
            new_name TEXT NOT NULL,
            change_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nce_symbol ON name_change_events(symbol)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nce_old_name ON name_change_events(old_name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nce_new_name ON name_change_events(new_name)")
    print("  Created name_change_events (3 indexes)")
    
    # Table 2: Symbol Changes
    print("\n[2/4] Creating symbol_change_events table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS symbol_change_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_symbol TEXT NOT NULL,
            new_symbol TEXT NOT NULL,
            company_name TEXT,
            change_date TEXT,
            listing_date_old TEXT,
            listing_date_new TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sce_old_symbol ON symbol_change_events(old_symbol)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sce_new_symbol ON symbol_change_events(new_symbol)")
    print("  Created symbol_change_events (2 indexes)")
    
    # Table 3: Corporate Events
    print("\n[3/4] Creating corporate_events table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS corporate_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            company_name TEXT,
            purpose TEXT NOT NULL,
            event_type TEXT,
            ex_date TEXT,
            record_date TEXT,
            bc_start_date TEXT,
            bc_end_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ce_symbol ON corporate_events(symbol)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ce_event_type ON corporate_events(event_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ce_ex_date ON corporate_events(ex_date)")
    print("  Created corporate_events (3 indexes)")
    
    # Table 4: Delistings
    print("\n[4/4] Creating delisting_events table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS delisting_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            company_name TEXT,
            last_traded_date TEXT,
            delisting_reason TEXT DEFAULT 'INFERRED',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_de_symbol ON delisting_events(symbol)")
    print("  Created delisting_events (1 index)")
    
    conn.commit()
    conn.close()

def verify_creation():
    """Verify all tables and indexes were created"""
    print("\n[VERIFICATION] Checking table creation...")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    expected_tables = [
        'name_change_events',
        'symbol_change_events',
        'corporate_events',
        'delisting_events'
    ]
    
    # Check tables exist
    cur.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name IN (?, ?, ?, ?)
    """, tuple(expected_tables))
    
    existing_tables = [row[0] for row in cur.fetchall()]
    
    missing = set(expected_tables) - set(existing_tables)
    
    if missing:
        print(f"  ERROR: Missing tables: {missing}")
        return False
    
    # Check index counts
    total_indexes = 0
    for table in expected_tables:
        cur.execute("""
            SELECT COUNT(*) FROM sqlite_master 
            WHERE type='index' AND tbl_name=?
        """, (table,))
        index_count = cur.fetchone()[0]
        total_indexes += index_count
    
    print(f"  Tables created: {len(existing_tables)}")
    print(f"  Indexes created: {total_indexes}")
    print("  Status: SUCCESS")
    
    conn.close()
    return True

def main():
    """Main execution"""
    
    # Create tables
    create_tables()
    
    # Verify
    if not verify_creation():
        print("\nERROR: Table creation failed")
        return 1
    
    print("\n" + "=" * 60)
    print("PHASE 2 COMPLETE")
    print("=" * 60)
    print("All tables and indexes created")
    print("Ready for Phase 3: Load Initial Data")
    
    return 0

if __name__ == '__main__':
    exit(main())
