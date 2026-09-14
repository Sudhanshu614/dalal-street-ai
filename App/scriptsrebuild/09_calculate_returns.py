"""
PHASE 9: CALCULATE RETURNS FROM HISTORICAL OHLCV
Calculates 1M, 3M, 6M, 1Y, 3Y, 5Y returns from existing daily_ohlc data

Formula: ((current_price - old_price) / old_price) * 100

Updates fundamentals table with returns data

Usage:
    python scripts/rebuild/09_calculate_returns.py
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB_FILE = Path(__file__).parent.parent.parent / "App" / "database" / "stock_market_new.db"

def log_message(message):
    print(message)

def calculate_returns_for_stock(cursor, symbol, current_price, current_date):
    """
    Calculate returns for a single stock

    Returns: dict with returns_1month, returns_3month, etc.
    """
    returns = {}

    try:
        # Define time periods (days ago)
        periods = {
            'returns_1month': 30,
            'returns_3month': 90,
            'returns_6month': 180,
            'returns_1year': 365,
            'returns_3year': 365 * 3,
            'returns_5year': 365 * 5,
        }

        for field_name, days_ago in periods.items():
            # Calculate target date
            target_date = (datetime.strptime(current_date, '%Y-%m-%d') - timedelta(days=days_ago)).strftime('%Y-%m-%d')

            # Find closest historical price (within 7 days of target)
            cursor.execute('''
                SELECT close, date
                FROM daily_ohlc
                WHERE symbol = ?
                  AND date <= ?
                  AND date >= ?
                ORDER BY date DESC
                LIMIT 1
            ''', (symbol, target_date, (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')))

            row = cursor.fetchone()
            if row:
                old_price, old_date = row
                if old_price > 0:
                    return_pct = ((current_price - old_price) / old_price) * 100
                    returns[field_name] = round(return_pct, 2)

        return returns

    except Exception as e:
        return {}

def calculate_all_returns():
    log_message("="*70)
    log_message("CALCULATE RETURNS FROM HISTORICAL OHLCV")
    log_message("="*70)
    log_message("")

    if not DB_FILE.exists():
        log_message("[ERROR] Database not found")
        return False

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    try:
        # Get all stocks that have OHLC data and exist in fundamentals
        # We use INNER JOIN to ensure we only try to calculate for stocks we can update
        cursor.execute("""
            SELECT DISTINCT f.symbol
            FROM fundamentals f
            INNER JOIN daily_ohlc o ON f.symbol = o.symbol
            ORDER BY f.symbol
        """)

        stocks = cursor.fetchall()
        total = len(stocks)

        log_message(f"[INFO] Found {total} stocks with OHLC data")
        log_message(f"[INFO] Calculating returns using OHLC as source of truth...")
        log_message("")

        success_count = 0
        failed_count = 0

        for i, (symbol,) in enumerate(stocks, 1):
            # Get latest OHLCV date and price for this stock
            # This is now the SINGLE SOURCE OF TRUTH for "current price" in returns calculation
            cursor.execute("""
                SELECT close, date
                FROM daily_ohlc
                WHERE symbol = ?
                ORDER BY date DESC
                LIMIT 1
            """, (symbol,))

            row = cursor.fetchone()
            if not row:
                log_message(f"[{i}/{total}] {symbol}: No OHLCV data found")
                failed_count += 1
                continue

            current_price, current_date = row

            # Calculate returns
            returns = calculate_returns_for_stock(cursor, symbol, current_price, current_date)

            if not returns:
                log_message(f"[{i}/{total}] {symbol}: Could not calculate returns (insufficient history)")
                failed_count += 1
                continue

            # Update database
            update_fields = []
            update_values = []

            for field_name, value in returns.items():
                update_fields.append(f"{field_name} = ?")
                update_values.append(value)

            if update_fields:
                update_values.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                update_values.append(symbol)

                sql = f"""
                    UPDATE fundamentals
                    SET {', '.join(update_fields)}, last_updated = ?
                    WHERE symbol = ?
                """

                cursor.execute(sql, update_values)
                conn.commit()

                # Show calculated returns
                returns_summary = []
                if returns.get('returns_1year'):
                    returns_summary.append(f"1Y: {returns['returns_1year']:+.1f}%")
                if returns.get('returns_3year'):
                    returns_summary.append(f"3Y: {returns['returns_3year']:+.1f}%")
                
                # Log less verbose output (one line per stock)
                log_message(f"[{i}/{total}] {symbol}: {', '.join(returns_summary) if returns_summary else 'Returns updated'}")
                success_count += 1
            else:
                log_message(f"[{i}/{total}] {symbol}: No returns calculated")
                failed_count += 1

        log_message("")
        log_message("="*70)
        log_message("SUMMARY")
        log_message("="*70)
        log_message(f"Total stocks: {total}")
        log_message(f"Success: {success_count}")
        log_message(f"Failed: {failed_count}")
        log_message("")
        log_message(f"[SUCCESS] Returns calculation complete!")
        log_message("")

        return True

    except Exception as e:
        log_message(f"[ERROR] Calculation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        conn.close()

if __name__ == "__main__":
    try:
        success = calculate_all_returns()
        exit(0 if success else 1)
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
