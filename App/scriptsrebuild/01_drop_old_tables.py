"""
Phase 1: Drop Old BSE-Contaminated Tables

This script safely drops the old ticker resolution tables that were built
from BSE data. These will be replaced with pure NSE authoritative data.

Tables to drop:
- stock_aliases (45,059 records - BSE contaminated)
- alias_events (7,061 records - fuzzy matched)
- company_names_canonical (2,222 records - redundant)
- name_changes_raw (163,602 records - raw BSE data)
- company_name_backfill_log (9,615 records - legacy)
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = 'App/database/stock_market_new.db'
BACKUP_PATH = f'App/database/backup_before_rebuild_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'

def create_backup():
    """Create full database backup before making changes"""
    print("=" * 60)
    print("PHASE 1: DROP OLD TABLES")
    print("=" * 60)
    print("\n[STEP 1/2] Creating database backup...")
    
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        return False
    
    try:
        # Connect to source database
        source_conn = sqlite3.connect(DB_PATH)
        
        # Create backup database
        backup_conn = sqlite3.connect(BACKUP_PATH)
        
        # Copy all data
        source_conn.backup(backup_conn)
        
        # Close connections
        source_conn.close()
        backup_conn.close()
        
        # Verify backup
        backup_size = os.path.getsize(BACKUP_PATH) / (1024 * 1024)  # MB
        print(f"  Backup created: {BACKUP_PATH}")
        print(f"  Backup size: {backup_size:.2f} MB")
        print("  Status: SUCCESS")
        return True
        
    except Exception as e:
        print(f"  ERROR creating backup: {e}")
        return False

def drop_old_tables():
    """Drop old BSE-contaminated tables"""
    print("\n[STEP 2/2] Dropping old tables...")
    
    tables_to_drop = [
        'stock_aliases',
        'alias_events',
        'company_names_canonical',
        'name_changes_raw',
        'company_name_backfill_log'
    ]
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    dropped_count = 0
    skipped_count = 0
    
    for table in tables_to_drop:
        try:
            # Check if table exists
            cur.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name=?
            """, (table,))
            
            if cur.fetchone():
                # Get record count before dropping
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                
                # Drop table
                cur.execute(f"DROP TABLE {table}")
                print(f"  Dropped {table} ({count:,} records)")
                dropped_count += 1
            else:
                print(f"  Skipped {table} (not found)")
                skipped_count += 1
                
        except Exception as e:
            print(f"  ERROR dropping {table}: {e}")
    
    conn.commit()
    conn.close()
    
    print(f"\n  Tables dropped: {dropped_count}")
    print(f"  Tables skipped: {skipped_count}")
    print("  Status: COMPLETE")

def verify_cleanup():
    """Verify old tables are gone"""
    print("\n[VERIFICATION] Checking cleanup...")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Get all remaining tables
    cur.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table'
        ORDER BY name
    """)
    
    tables = [row[0] for row in cur.fetchall()]
    
    old_tables = [
        'stock_aliases',
        'alias_events', 
        'company_names_canonical',
        'name_changes_raw',
        'company_name_backfill_log'
    ]
    
    remaining_old = [t for t in old_tables if t in tables]
    
    if remaining_old:
        print(f"  WARNING: Old tables still exist: {remaining_old}")
        print("  Status: FAILED")
        return False
    else:
        print(f"  All old tables removed")
        print(f"  Remaining tables: {len(tables)}")
        print("  Status: SUCCESS")
        return True

def main():
    """Main execution"""
    
    # Step 1: Create backup
    if not create_backup():
        print("\nABORTED: Backup failed")
        return 1
    
    # Step 2: Drop old tables
    drop_old_tables()
    
    # Step 3: Verify
    if not verify_cleanup():
        print("\nWARNING: Verification failed")
        return 1
    
    print("\n" + "=" * 60)
    print("PHASE 1 COMPLETE")
    print("=" * 60)
    print(f"Backup location: {BACKUP_PATH}")
    print("Ready for Phase 2: Create New Tables")
    
    return 0

if __name__ == '__main__':
    exit(main())
