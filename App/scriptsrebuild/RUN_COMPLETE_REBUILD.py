"""
ONE-COMMAND COMPLETE DATABASE REBUILD
Runs all phases automatically - just run and wait

What it does:
1. Checks dependencies
2. Backs up old database
3. Creates new schema
4. Downloads all data (2-3 hours)
5. Validates quality
6. Processes stock_aliases (27 CSV files from 1999-2025)
7. Retries failed downloads
8. Generates final report

NEW: Automatically processes stock_aliases folder!
- 27 CSV files (1999-2025) with 2,673 company name changes
- AI chatbot will recognize old names (e.g., "Zomato" → "ETERNAL")
- Generates data/symbol_mappings.py for AI integration

Usage:
    python scripts/rebuild/RUN_COMPLETE_REBUILD.py

Then wait 2-3 hours. Check logs/FINAL_REPORT.txt when done.
"""

import sys
import os
import subprocess
import time
from pathlib import Path
from datetime import datetime
import shutil

# Colors for terminal (works on Linux VM)
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_header(message):
    print("\n" + "="*70)
    print(f"{BLUE}{message}{RESET}")
    print("="*70 + "\n")

def print_success(message):
    print(f"{GREEN}[SUCCESS]{RESET} {message}")

def print_error(message):
    print(f"{RED}[ERROR]{RESET} {message}")

def print_info(message):
    print(f"{BLUE}[INFO]{RESET} {message}")

def print_warn(message):
    print(f"{YELLOW}[WARN]{RESET} {message}")

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts" / "rebuild"
DB_DIR = PROJECT_ROOT / "database"
LOG_DIR = PROJECT_ROOT / "logs"

START_TIME = datetime.now()
LOG_FILE = LOG_DIR / f"COMPLETE_REBUILD_{START_TIME.strftime('%Y%m%d_%H%M%S')}.log"
FINAL_REPORT = LOG_DIR / "FINAL_REPORT.txt"

def log_and_print(message):
    """Log to file and print to console"""
    print(message)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(message + '\n')

def run_command(description, command, critical=True):
    """Run a command and handle errors"""
    print_info(f"{description}...")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            shell=True if isinstance(command, str) else False
        )

        if result.returncode == 0:
            print_success(f"{description} - DONE")
            return True, result.stdout
        else:
            print_error(f"{description} - FAILED")
            print(result.stderr)

            if critical:
                print_error("Critical step failed. Aborting.")
                return False, result.stderr
            else:
                print_warn("Non-critical step failed. Continuing...")
                return False, result.stderr

    except Exception as e:
        print_error(f"{description} - EXCEPTION: {e}")
        if critical:
            return False, str(e)
        return False, str(e)

def check_dependencies():
    """Check if all dependencies are installed"""
    print_header("STEP 1: CHECKING DEPENDENCIES")

    # Check Python version
    python_version = sys.version.split()[0]
    print_info(f"Python version: {python_version}")

    # Check openchart
    try:
        import openchart
        print_success("openchart - installed")
    except ImportError:
        print_error("openchart - NOT installed")
        print_info("Installing openchart...")
        subprocess.run([sys.executable, "-m", "pip", "install", "openchart"], check=True)

    # Check nselib
    try:
        import nselib
        print_success("nselib - installed")
    except ImportError:
        print_error("nselib - NOT installed")
        print_info("Attempting to install from nselib-2.0/...")

        nselib_dir = PROJECT_ROOT / "nselib-2.0"
        if nselib_dir.exists():
            subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(nselib_dir)], check=True)
        else:
            print_error("nselib-2.0 directory not found!")
            return False

    # Check requests, beautifulsoup4
    required_packages = ['requests', 'beautifulsoup4', 'pandas']
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
            print_success(f"{package} - installed")
        except ImportError:
            print_info(f"Installing {package}...")
            subprocess.run([sys.executable, "-m", "pip", "install", package], check=True)

    print_success("All dependencies installed")
    return True

def backup_database():
    """Backup current database"""
    print_header("STEP 2: BACKING UP CURRENT DATABASE")

    old_db = DB_DIR / "stock_market.db"

    if not old_db.exists():
        print_warn("No existing database found. Skipping backup.")
        return True

    backup_name = f"stock_market_backup_{START_TIME.strftime('%Y%m%d_%H%M%S')}.db"
    backup_path = DB_DIR / backup_name

    try:
        shutil.copy2(old_db, backup_path)
        size_mb = backup_path.stat().st_size / (1024 * 1024)
        print_success(f"Backup created: {backup_name} ({size_mb:.2f} MB)")
        return True
    except Exception as e:
        print_error(f"Backup failed: {e}")
        return False

def create_schema():
    """Create new database schema"""
    print_header("STEP 3: CREATING DATABASE SCHEMA")

    success, output = run_command(
        "Creating schema",
        [sys.executable, str(SCRIPTS_DIR / "01_create_schema.py")],
        critical=True
    )

    return success

def download_data():
    """Download all data"""
    print_header("STEP 4: DOWNLOADING ALL DATA (This takes 2-3 hours)")

    print_info("Stock Master: ~3 seconds")
    print_info("Historical OHLCV: ~60 minutes")
    print_info("Fundamentals: ~72 minutes")
    print_info("Corporate Actions: ~2 seconds")
    print("")
    print_info("You can safely leave this running...")
    print("")

    success, output = run_command(
        "Downloading all data",
        [sys.executable, str(SCRIPTS_DIR / "02_master_rebuild.py")],
        critical=True
    )

    return success

def validate_database():
    """Validate database quality"""
    print_header("STEP 5: VALIDATING DATABASE QUALITY")

    success, output = run_command(
        "Running validation checks",
        [sys.executable, str(SCRIPTS_DIR / "03_validate_database.py")],
        critical=False  # Non-critical - we still want report even if validation finds issues
    )

    return success, output

def retry_failed_downloads():
    """Retry failed downloads by checking download_log"""
    print_header("STEP 6: RETRYING FAILED DOWNLOADS")

    import sqlite3

    db_file = DB_DIR / "stock_market_new.db"

    if not db_file.exists():
        print_error("Database not found. Cannot retry.")
        return False

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Get failed symbols
    cursor.execute("""
        SELECT DISTINCT symbol
        FROM download_log
        WHERE status = 'FAILED' AND symbol IS NOT NULL
    """)

    failed_symbols = [row[0] for row in cursor.fetchall()]
    conn.close()

    if not failed_symbols:
        print_success("No failed downloads to retry")
        return True

    print_info(f"Found {len(failed_symbols)} failed symbols. Retrying...")

    # Re-run download script will skip successful ones
    success, output = run_command(
        "Re-running downloads",
        [sys.executable, str(SCRIPTS_DIR / "02_master_rebuild.py")],
        critical=False
    )

    return success

def generate_final_report():
    """Generate comprehensive final report"""
    print_header("STEP 7: GENERATING FINAL REPORT")

    import sqlite3

    db_file = DB_DIR / "stock_market_new.db"

    if not db_file.exists():
        print_error("Database not found. Cannot generate report.")
        return False

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    report = []
    report.append("="*70)
    report.append("DATABASE REBUILD - FINAL REPORT")
    report.append("="*70)
    report.append(f"Started: {START_TIME.strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"Duration: {(datetime.now() - START_TIME).total_seconds() / 60:.1f} minutes")
    report.append("")

    # Stock Master
    cursor.execute("SELECT COUNT(*) FROM stocks_master")
    total_stocks = cursor.fetchone()[0]
    report.append(f"Stock Master: {total_stocks} stocks")

    # OHLCV
    cursor.execute("SELECT COUNT(*) FROM daily_ohlc")
    total_ohlc = cursor.fetchone()[0]
    cursor.execute("SELECT MIN(date), MAX(date) FROM daily_ohlc")
    min_date, max_date = cursor.fetchone()
    report.append(f"Daily OHLCV: {total_ohlc:,} records ({min_date} to {max_date})")

    # Fundamentals
    cursor.execute("SELECT COUNT(*) FROM fundamentals")
    total_fundamentals = cursor.fetchone()[0]
    report.append(f"Fundamentals: {total_fundamentals} stocks")

    # Corporate Actions
    cursor.execute("SELECT COUNT(*) FROM corporate_actions")
    total_actions = cursor.fetchone()[0]
    report.append(f"Corporate Actions: {total_actions:,} records")

    # Database size
    db_size_mb = db_file.stat().st_size / (1024 * 1024)
    report.append(f"Database Size: {db_size_mb:.2f} MB")

    report.append("")
    report.append("="*70)
    report.append("DATA QUALITY CHECKS")
    report.append("="*70)

    # NULL names check
    cursor.execute("SELECT COUNT(*) FROM stocks_master WHERE company_name IS NULL OR company_name = ''")
    null_names = cursor.fetchone()[0]
    status = "PASS" if null_names == 0 else "FAIL"
    report.append(f"NULL company names: {null_names} [{status}]")

    # Negative prices check
    cursor.execute("SELECT COUNT(*) FROM daily_ohlc WHERE close < 0")
    negative_prices = cursor.fetchone()[0]
    status = "PASS" if negative_prices == 0 else "FAIL"
    report.append(f"Negative prices: {negative_prices} [{status}]")

    # Coverage checks
    cursor.execute("SELECT COUNT(*) FROM fundamentals WHERE market_cap IS NOT NULL")
    mc_count = cursor.fetchone()[0]
    mc_percent = (mc_count / total_fundamentals * 100) if total_fundamentals > 0 else 0
    report.append(f"Market Cap coverage: {mc_count}/{total_fundamentals} ({mc_percent:.1f}%)")

    cursor.execute("SELECT COUNT(*) FROM fundamentals WHERE pe_ratio IS NOT NULL")
    pe_count = cursor.fetchone()[0]
    pe_percent = (pe_count / total_fundamentals * 100) if total_fundamentals > 0 else 0
    report.append(f"PE Ratio coverage: {pe_count}/{total_fundamentals} ({pe_percent:.1f}%)")

    cursor.execute("SELECT COUNT(*) FROM fundamentals WHERE pb_ratio IS NOT NULL")
    pb_count = cursor.fetchone()[0]
    pb_percent = (pb_count / total_fundamentals * 100) if total_fundamentals > 0 else 0
    report.append(f"PB Ratio coverage: {pb_count}/{total_fundamentals} ({pb_percent:.1f}%)")

    report.append("")
    report.append("="*70)
    report.append("DOWNLOAD STATISTICS")
    report.append("="*70)

    # Success/failure stats
    cursor.execute("""
        SELECT table_name, status, COUNT(*)
        FROM download_log
        GROUP BY table_name, status
        ORDER BY table_name, status
    """)

    stats = cursor.fetchall()
    current_table = None

    for table, status, count in stats:
        if table != current_table:
            report.append(f"\n{table}:")
            current_table = table
        report.append(f"  {status}: {count}")

    # Stock Aliases (check BEFORE closing connection)
    aliases_file = PROJECT_ROOT / "data" / "symbol_mappings.py"
    try:
        cursor.execute("SELECT COUNT(*) FROM stock_aliases")
        total_aliases = cursor.fetchone()[0]
        report.append(f"\nStock Aliases: {total_aliases} mappings (1999-2025)")
        report.append(f"  - AI can now recognize old company names!")
        report.append(f"  - Example: User says 'Zomato' → System maps to 'ETERNAL'")
    except:
        if aliases_file.exists():
            report.append("\nStock Aliases: Processed (check data/symbol_mappings.py)")
        else:
            report.append("\nStock Aliases: Not processed (non-critical)")

    conn.close()

    report.append("")
    report.append("="*70)
    report.append("NEXT STEPS")
    report.append("="*70)
    report.append("1. Review this report")
    report.append("2. Check detailed logs in: logs/")
    report.append("3. Switch to new database:")
    report.append("   - mv database/stock_market.db database/stock_market_old.db")
    report.append("   - mv database/stock_market_new.db database/stock_market.db")
    report.append("4. Update backend to use new database")
    report.append("5. Integrate symbol_mappings.py in AI chatbot:")
    report.append("   - from data.symbol_mappings import SYMBOL_MAPPINGS")
    report.append("   - Use to map old company names to current symbols")
    report.append("")
    report.append("="*70)
    report.append("REBUILD COMPLETE!")
    report.append("="*70)

    # Write report to file
    report_text = '\n'.join(report)

    with open(FINAL_REPORT, 'w', encoding='utf-8') as f:
        f.write(report_text)

    print(report_text)

    print("")
    print_success(f"Final report saved to: {FINAL_REPORT}")

    return True

def process_stock_aliases():
    """Process stock_aliases CSV files (27 files from 1999-2025)"""
    print_header("STEP 6: PROCESSING STOCK ALIASES")

    # Check if stock_aliases folder exists
    aliases_dir = PROJECT_ROOT / "stock_aliases"

    if not aliases_dir.exists():
        print_warn("stock_aliases folder not found. Skipping...")
        print_info("User can run manually later: python scripts/rebuild/04_process_stock_aliases.py")
        return False

    csv_files = list(aliases_dir.glob("Comp_Name_*.csv"))

    if not csv_files:
        print_warn("No CSV files found in stock_aliases/. Skipping...")
        return False

    print_info(f"Found {len(csv_files)} CSV files (1999-2025)")
    print_info("Processing all name changes...")

    success, output = run_command(
        "Processing stock aliases",
        [sys.executable, str(SCRIPTS_DIR / "04_process_stock_aliases.py")],
        critical=False  # Non-critical - chatbot will work without it
    )

    if success:
        print_success("Stock aliases processed successfully!")
        print_info("AI chatbot can now handle old company names (e.g., 'Zomato' → 'ETERNAL')")
    else:
        print_warn("Stock aliases processing failed (non-critical)")
        print_info("AI chatbot will work, but won't recognize old company names")

    return success

def main():
    """Main execution flow"""

    print_header("COMPLETE DATABASE REBUILD - ONE COMMAND")
    print_info(f"Started: {START_TIME.strftime('%Y-%m-%d %H:%M:%S')}")
    print_info(f"Log file: {LOG_FILE}")
    print("")

    # Step 1: Check dependencies
    if not check_dependencies():
        print_error("Dependency check failed. Aborting.")
        return False

    # Step 2: Backup
    if not backup_database():
        response = input("Backup failed. Continue anyway? (yes/no): ")
        if response.lower() != 'yes':
            print_error("Aborted by user.")
            return False

    # Step 3: Create schema
    if not create_schema():
        print_error("Schema creation failed. Aborting.")
        return False

    # Step 4: Download data (LONG RUNNING - 2-3 hours)
    print("")
    print_warn("="*70)
    print_warn("ENTERING LONG-RUNNING PHASE (2-3 hours)")
    print_warn("You can safely leave this running.")
    print_warn("Check logs/ directory for progress.")
    print_warn("="*70)
    print("")

    if not download_data():
        print_error("Data download had issues. Continuing to validation...")

    # Step 5: Validate
    validation_passed, validation_output = validate_database()

    # Step 6: Process stock_aliases (27 CSV files from 1999-2025)
    process_stock_aliases()

    # Step 7: Retry failed downloads
    retry_failed_downloads()

    # Step 8: Final report
    generate_final_report()

    # Summary
    elapsed = datetime.now() - START_TIME
    print("")
    print_header("REBUILD COMPLETE!")
    print_success(f"Total time: {elapsed.total_seconds() / 60:.1f} minutes")
    print_success(f"Final report: {FINAL_REPORT}")
    print_success(f"Detailed logs: {LOG_FILE}")
    print("")
    print_info("Read FINAL_REPORT.txt for next steps")

    return True

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("")
        print_warn("\nInterrupted by user. Progress has been saved.")
        print_warn("Re-run this script to resume.")
        sys.exit(1)
    except Exception as e:
        print_error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
