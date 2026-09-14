import sqlite3
import pandas as pd
import os
from datetime import datetime
from pathlib import Path
import logging

try:
    from App.config import config as _app_config

    _CSV_DIR = Path(_app_config.CSV_DIRECTORY)
except Exception:  # pragma: no cover - standalone execution fallback
    # App/src/lifecycle/lifecycle_manager.py -> lifecycle -> src -> App -> root
    _PROJECT_ROOT = Path(__file__).resolve().parents[3]
    _CSV_DIR = Path(
        os.getenv("CSV_DIRECTORY", _PROJECT_ROOT / "App" / "database")
    )


def _latest_csv(directory: Path, *patterns: str, fallback: str) -> Path:
    """Return the newest CSV matching any pattern, else `directory / fallback`."""
    for pattern in patterns:
        matches = sorted(directory.glob(pattern))
        if matches:
            return matches[-1]
    return directory / fallback

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('lifecycle_manager.log'),
        logging.StreamHandler()
    ]
)

class LifecycleManager:
    def __init__(self, db_path='company_lifecycle.db', csv_directory=None):
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        
        # Source CSVs, discovered under CSV_DIRECTORY (see App/config.py).
        csv_dir = Path(csv_directory) if csv_directory else _CSV_DIR
        self.csv_directory = csv_dir
        self.files = {
            'stock_master': csv_dir / 'stock_master.csv',
            'namechange': csv_dir / 'namechange.csv',
            'symbolchange': csv_dir / 'symbolchange.csv',
            # NSE exports carry a date range in the filename, so pick the newest.
            'cf_ca': _latest_csv(
                csv_dir, 'CF-CA-equities-*.csv', 'CF-CA-*.csv',
                fallback='CF-CA-equities.csv',
            ),
            'ipo': _latest_csv(
                csv_dir, 'IPO-PastIssue-*.csv',
                fallback='IPO-PastIssue.csv',
            ),
        }

    def connect(self):
        """Connect to the SQLite database."""
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.cursor = self.conn.cursor()
            logging.info(f"Connected to database: {self.db_path}")
        except Exception as e:
            logging.error(f"Database connection failed: {e}")
            raise

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            logging.info("Database connection closed.")

    def initialize_db(self, schema_path=None):
        """Initialize the database with the schema."""
        logging.info("Initializing database schema...")
        if schema_path is None:
            schema_path = Path(__file__).resolve().parent / 'schema.sql'
        try:
            with open(schema_path, 'r') as f:
                schema_sql = f.read()
            
            self.cursor.executescript(schema_sql)
            self.conn.commit()
            logging.info("Schema initialized successfully.")
        except Exception as e:
            logging.error(f"Schema initialization failed: {e}")
            raise

    def _parse_date(self, date_str):
        """Helper to parse dates from various formats."""
        if pd.isna(date_str) or str(date_str).strip() == '' or str(date_str).strip() == '-':
            return None
        
        formats = ['%d-%b-%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y']
        for fmt in formats:
            try:
                return datetime.strptime(str(date_str).strip(), fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
        return None

    def load_stock_master(self):
        """Load current companies from stock_master.csv."""
        logging.info("Loading stock_master.csv...")
        try:
            df = pd.read_csv(self.files['stock_master'])
            
            # Clean column names
            df.columns = [c.strip() for c in df.columns]
            
            count = 0
            for _, row in df.iterrows():
                symbol = row.get('SYMBOL')
                name = row.get('NAME OF COMPANY')
                series = row.get('SERIES')
                listing_date = self._parse_date(row.get('DATE OF LISTING'))
                isin = row.get('ISIN NUMBER')
                face_value = row.get('FACE VALUE')
                
                if not symbol:
                    continue

                # Insert into master_companies
                self.cursor.execute('''
                    INSERT INTO master_companies (
                        current_symbol, current_name, original_symbol, original_name,
                        isin_number, date_of_first_listing, current_status,
                        face_value, series
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symbol, name, symbol, name,
                    isin, listing_date, 'LISTED',
                    face_value, series
                ))
                count += 1
            
            self.conn.commit()
            logging.info(f"Loaded {count} companies from stock_master.")
            
        except Exception as e:
            logging.error(f"Failed to load stock_master: {e}")
            raise

    def load_ipo_data(self):
        """Load IPO history and identify potential delistings."""
        logging.info("Loading IPO-PastIssue.csv...")
        try:
            df = pd.read_csv(self.files['ipo'])
            
            # Clean column names
            df.columns = [c.strip() for c in df.columns]
            
            count = 0
            linked_count = 0
            new_count = 0
            
            for _, row in df.iterrows():
                symbol = row.get('Symbol')
                name = row.get('COMPANY NAME')
                listing_date = self._parse_date(row.get('DATE OF LISTING'))
                security_type = row.get('SECURITY TYPE')
                issue_price = row.get('ISSUE PRICE')
                price_range = row.get('PRICE RANGE')
                
                if not symbol or pd.isna(symbol) or symbol == '-':
                    continue

                # Check if company exists in master (by symbol)
                # NOTE: In a real scenario, we'd check name too, but symbol is our best bet for now
                self.cursor.execute('SELECT company_id FROM master_companies WHERE current_symbol = ?', (symbol,))
                result = self.cursor.fetchone()
                
                company_id = None
                if result:
                    company_id = result[0]
                    linked_count += 1
                else:
                    # If not in master, it might be DELISTED or Name Changed
                    # For now, we'll insert it into IPO listings with NULL company_id
                    # Later, the Symbol Resolver will try to link these
                    new_count += 1

                # Insert into ipo_listings
                self.cursor.execute('''
                    INSERT INTO ipo_listings (
                        company_id, company_name_at_ipo, symbol_at_ipo,
                        security_type, issue_price, price_range, date_of_listing
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    company_id, name, symbol,
                    security_type, issue_price, price_range, listing_date
                ))
                
                # If we found a company_id, update its original listing date if it's missing
                if company_id and listing_date:
                    self.cursor.execute('''
                        UPDATE master_companies 
                        SET date_of_first_listing = ? 
                        WHERE company_id = ? AND date_of_first_listing IS NULL
                    ''', (listing_date, company_id))

                count += 1
            
            self.conn.commit()
            logging.info(f"Loaded {count} IPO records.")
            logging.info(f"Linked {linked_count} IPOs to current companies.")
            logging.info(f"Found {new_count} IPOs not in current master (Potential Delistings/Name Changes).")
            
        except Exception as e:
            logging.error(f"Failed to load IPO data: {e}")
            raise

    def load_name_changes(self):
        """Load name change history."""
        logging.info("Loading namechange.csv...")
        try:
            df = pd.read_csv(self.files['namechange'])
            # Clean column names (handle leading spaces)
            df.columns = [c.strip() for c in df.columns]
            
            count = 0
            for _, row in df.iterrows():
                symbol = row.get('NCH_SYMBOL')
                prev_name = row.get('NCH_PREV_NAME')
                new_name = row.get('NCH_NEW_NAME')
                date_str = row.get('NCH_DT')
                change_date = self._parse_date(date_str)
                
                if not symbol:
                    continue

                # Try to find company_id
                self.cursor.execute('SELECT company_id FROM master_companies WHERE current_symbol = ?', (symbol,))
                result = self.cursor.fetchone()
                company_id = result[0] if result else -1  # -1 for unknown/delisted

                self.cursor.execute('''
                    INSERT INTO name_change_history (
                        company_id, symbol_at_time, previous_name, new_name, change_date, source
                    ) VALUES (?, ?, ?, ?, ?, ?)
                ''', (company_id, symbol, prev_name, new_name, change_date, 'namechange.csv'))
                count += 1
            
            self.conn.commit()
            logging.info(f"Loaded {count} name change records.")
            
        except Exception as e:
            logging.error(f"Failed to load name changes: {e}")

    def load_symbol_changes(self):
        """Load symbol change history."""
        logging.info("Loading symbolchange.csv...")
        try:
            # No header in this file
            df = pd.read_csv(self.files['symbolchange'], header=None, 
                           names=['company_name', 'old_symbol', 'new_symbol', 'change_date'])
            
            count = 0
            for _, row in df.iterrows():
                old_sym = row.get('old_symbol')
                new_sym = row.get('new_symbol')
                name = row.get('company_name')
                date_str = row.get('change_date')
                change_date = self._parse_date(date_str)
                
                if not old_sym or not new_sym:
                    continue

                # Try to find company_id using NEW symbol (most likely to be in master if recent)
                self.cursor.execute('SELECT company_id FROM master_companies WHERE current_symbol = ?', (new_sym,))
                result = self.cursor.fetchone()
                
                # If not found, try OLD symbol
                if not result:
                    self.cursor.execute('SELECT company_id FROM master_companies WHERE current_symbol = ?', (old_sym,))
                    result = self.cursor.fetchone()
                
                company_id = result[0] if result else -1

                self.cursor.execute('''
                    INSERT INTO symbol_change_history (
                        company_id, previous_symbol, new_symbol, name_at_time, change_date, source
                    ) VALUES (?, ?, ?, ?, ?, ?)
                ''', (company_id, old_sym, new_sym, name, change_date, 'symbolchange.csv'))
                count += 1
            
            self.conn.commit()
            logging.info(f"Loaded {count} symbol change records.")
            
        except Exception as e:
            logging.error(f"Failed to load symbol changes: {e}")

    def load_corporate_actions(self):
        """Load corporate actions and extract hidden lifecycle events."""
        logging.info("Loading CF-CA-equities...")
        try:
            df = pd.read_csv(self.files['cf_ca'])
            df.columns = [c.strip() for c in df.columns]
            
            count = 0
            event_counts = {'MERGER': 0, 'DEMERGER': 0, 'DELISTING': 0}
            
            for _, row in df.iterrows():
                symbol = row.get('SYMBOL')
                purpose = str(row.get('PURPOSE')).upper()
                ex_date = self._parse_date(row.get('EX-DATE'))
                
                if not symbol or not ex_date:
                    continue

                # Find company
                self.cursor.execute('SELECT company_id FROM master_companies WHERE current_symbol = ?', (symbol,))
                result = self.cursor.fetchone()
                company_id = result[0] if result else -1

                # 1. General Corporate Action Insert
                self.cursor.execute('''
                    INSERT INTO corporate_actions (
                        company_id, symbol_at_time, purpose, ex_date, source
                    ) VALUES (?, ?, ?, ?, ?)
                ''', (company_id, symbol, row.get('PURPOSE'), ex_date, 'CF-CA'))
                
                # 2. Extract Special Events
                if 'MERGER' in purpose or 'AMALGAMATION' in purpose:
                    self.cursor.execute('''
                        INSERT INTO merger_demerger_events (
                            event_type, event_date, company_id_acquiring, description
                        ) VALUES (?, ?, ?, ?)
                    ''', ('MERGER', ex_date, company_id, row.get('PURPOSE')))
                    event_counts['MERGER'] += 1
                    
                elif 'DEMERGER' in purpose or 'SPIN OFF' in purpose:
                    self.cursor.execute('''
                        INSERT INTO merger_demerger_events (
                            event_type, event_date, company_id_acquiring, description
                        ) VALUES (?, ?, ?, ?)
                    ''', ('DEMERGER', ex_date, company_id, row.get('PURPOSE')))
                    event_counts['DEMERGER'] += 1
                    
                elif 'DELIST' in purpose:
                    self.cursor.execute('''
                        INSERT INTO delisting_events (
                            company_id, symbol_at_delisting, delisting_date, delisting_reason, source
                        ) VALUES (?, ?, ?, ?, ?)
                    ''', (company_id, symbol, ex_date, row.get('PURPOSE'), 'CF-CA'))
                    event_counts['DELISTING'] += 1

                count += 1
            
            self.conn.commit()
            logging.info(f"Loaded {count} corporate actions.")
            logging.info(f"Extracted Events: {event_counts}")
            
        except Exception as e:
            logging.error(f"Failed to load corporate actions: {e}")

if __name__ == "__main__":
    manager = LifecycleManager()
    try:
        manager.connect()
        # manager.initialize_db() # Skip if already done, or use a flag
        # For now, we'll assume fresh start for simplicity in this run
        manager.initialize_db() 
        
        manager.load_stock_master()
        manager.load_ipo_data()
        manager.load_name_changes()
        manager.load_symbol_changes()
        manager.load_corporate_actions()
        
        print("Full database population complete.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        manager.close()
