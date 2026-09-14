"""
PHASE 7: MIGRATE - ADD ENHANCED FIELDS & CREATE NEW TABLES
Part 1: Adds enhanced fields to fundamentals table
Part 2: Creates quarterly_results and annual_financials tables

New fields being added to fundamentals:
- Industry/Sector classification (4 fields)
- Returns data (6 fields)
- Growth metrics (8 fields)
- Debt metrics (2 fields - debt_to_equity + bank deposits)

New tables being created:
- quarterly_results (quarterly financial data)
- annual_financials (annual P&L + balance sheet data)

Usage:
    python scripts/rebuild/07_migrate_add_enhanced_fields.py
"""

import sqlite3
from pathlib import Path
from datetime import datetime

DB_FILE = Path(__file__).parent.parent.parent / "database" / "stock_market_new.db"

def log_message(message):
    print(message)

def migrate_add_fields():
    log_message("="*70)
    log_message("MIGRATE: ADD ENHANCED FIELDS TO FUNDAMENTALS")
    log_message("="*70)
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found")
        return False

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    try:
        log_message("[INFO] Adding new fields to fundamentals table...")
        log_message("")

        # Get existing columns
        cursor.execute("PRAGMA table_info(fundamentals)")
        existing_columns = [col[1] for col in cursor.fetchall()]

        # Define new fields to add
        new_fields = [
            # Industry/Sector Classification
            ("industry", "TEXT", "Industry category (e.g., 'Automobiles', 'Banks')"),
            ("sector", "TEXT", "Sector category (e.g., 'Consumer Discretionary', 'Financials')"),
            ("subsector", "TEXT", "Sub-sector category"),
            ("business_segment", "TEXT", "Specific business segments"),

            # Returns Data (%)
            ("returns_1month", "REAL", "1-month return %"),
            ("returns_3month", "REAL", "3-month return %"),
            ("returns_6month", "REAL", "6-month return %"),
            ("returns_1year", "REAL", "1-year return %"),
            ("returns_3year", "REAL", "3-year return %"),
            ("returns_5year", "REAL", "5-year return %"),

            # Growth Metrics (CAGR %)
            ("sales_growth_3year", "REAL", "Sales CAGR 3 years %"),
            ("sales_growth_5year", "REAL", "Sales CAGR 5 years %"),
            ("sales_growth_10year", "REAL", "Sales CAGR 10 years %"),
            ("profit_growth_3year", "REAL", "Profit CAGR 3 years %"),
            ("profit_growth_5year", "REAL", "Profit CAGR 5 years %"),
            ("profit_growth_10year", "REAL", "Profit CAGR 10 years %"),
            ("eps_growth_3year", "REAL", "EPS CAGR 3 years %"),
            ("eps_growth_5year", "REAL", "EPS CAGR 5 years %"),

            # Debt Metrics
            ("debt_to_equity", "REAL", "Debt to Equity ratio"),

            # Bank-specific metrics
            ("total_deposits", "REAL", "Total Deposits (for banks, in Crores)"),
        ]

        added_count = 0
        skipped_count = 0

        for field_name, field_type, description in new_fields:
            if field_name in existing_columns:
                log_message(f"[SKIP] {field_name} - already exists")
                skipped_count += 1
            else:
                cursor.execute(f"ALTER TABLE fundamentals ADD COLUMN {field_name} {field_type}")
                log_message(f"[ADD] {field_name} ({field_type}) - {description}")
                added_count += 1

        conn.commit()

        log_message("")
        log_message("="*70)
        log_message("MIGRATION SUMMARY")
        log_message("="*70)
        log_message(f"Fields added: {added_count}")
        log_message(f"Fields skipped (already exist): {skipped_count}")
        log_message(f"Total new fields: {len(new_fields)}")
        log_message("")

        if added_count > 0:
            log_message("[SUCCESS] Migration complete!")
            log_message("")
            log_message("Next steps:")
            log_message("  1. Run: python scripts/rebuild/08_scrape_enhanced_fundamentals.py")
            log_message("  2. This will populate all new fields for 2,103+ stocks")
            log_message("")
        else:
            log_message("[INFO] All fields already exist. Database already migrated.")

        return True

    except Exception as e:
        log_message(f"[ERROR] Migration failed: {e}")
        conn.rollback()
        import traceback
        traceback.print_exc()
        return False

    finally:
        conn.close()

if __name__ == "__main__":
    try:
        success = migrate_add_fields()
        exit(0 if success else 1)
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
