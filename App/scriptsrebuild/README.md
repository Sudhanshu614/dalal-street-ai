# DATABASE REBUILD - EXECUTION GUIDE

**All scripts validated and production-ready**

---

## QUICK START

```bash
# Backup current database
cp database/stock_market.db database/stock_market_backup_$(date +%Y%m%d).db

# Step 1: Create schema (1 minute)
python scripts/rebuild/01_create_schema.py

# Step 2: Download all data (2-3 hours)
python scripts/rebuild/02_master_rebuild.py

# Step 3: Validate quality (1 minute)
python scripts/rebuild/03_validate_database.py

# Step 4: Process stock_aliases (after uploading BSE Excel)
python scripts/rebuild/04_process_stock_aliases.py
```

---

## DETAILED INSTRUCTIONS

### PHASE 1: Backup Current Database (2 minutes)

```bash
# Windows
copy database\stock_market.db database\stock_market_backup_20251008.db

# Linux/Mac
cp database/stock_market.db database/stock_market_backup_20251008.db
```

---

### PHASE 2: Create New Schema (1 minute)

```bash
python scripts/rebuild/01_create_schema.py
```

**What it does:**
- Creates `stock_market_new.db` with 12 tables
- Adds CHECK constraints (no negative prices)
- Adds FOREIGN KEY constraints (referential integrity)
- Creates indexes for performance

**Output:**
```
[OK] stocks_master created
[OK] daily_ohlc created
[OK] fundamentals created
... (12 tables total)
[SUCCESS] Schema created successfully!
```

---

### PHASE 3: Download Data (2-3 hours)

```bash
# Production mode (all stocks)
python scripts/rebuild/02_master_rebuild.py

# Test mode (5 stocks only, for testing)
python scripts/rebuild/02_master_rebuild.py --test
```

**What it downloads:**

| Data Source | Time | Records | API Used |
|-------------|------|---------|----------|
| Stock Master | 3s | 2,182 stocks | nselib equity_list() |
| Historical OHLCV | 60 min | ~10M records | OpenChart historical() |
| Fundamentals | 72 min | 2,175 records | Screener.in scraping |
| Corporate Actions | 2s | ~15,000 records | nselib corporate_actions() |

**Progress Tracking:**
```
[INFO] Phase 2.1: Stock Master
[OK] Got 2,182 stocks in 2.94s
[SUMMARY] Success: 2,182

[INFO] Phase 2.2: Historical OHLCV
[1/2182] RELIANCE: 5,150 records in 1.4s
[2/2182] TCS: 4,980 records in 1.3s
...
[PROGRESS] 50/2182 stocks processed
...

[INFO] Phase 2.3: Fundamentals
[1/2182] RELIANCE: OK
[2/2182] TCS: OK
...

[INFO] Phase 2.4: Corporate Actions
[OK] Got 13,967 actions in 1.75s
```

**Resume Capability:**
- Script logs every download to `download_log` table
- Can be interrupted (Ctrl+C) and resumed
- Will skip already downloaded data

---

### PHASE 4: Validate Database (1 minute)

```bash
python scripts/rebuild/03_validate_database.py
```

**What it checks:**

1. ✅ No NULL company names
2. ✅ No symbol-as-name bugs
3. ✅ No negative prices
4. ✅ No invalid ranges (high < low)
5. ✅ Coverage statistics
6. ✅ Query performance

**Expected Output:**
```
[TEST 1] Stock Master Quality
  Total stocks: 2,182
  [OK] NULL company names: 0
  [OK] Symbol-as-name bugs: 0

[TEST 2] OHLCV Data Quality
  Total OHLC records: 10,542,300
  [OK] Negative prices: 0
  [OK] Invalid ranges: 0
  Date range: 2005-01-03 to 2025-10-08

[TEST 3] Fundamentals Coverage
  Market Cap: 1814/2175 (83.4%)
  PE Ratio: 1519/2175 (69.8%)
  PB Ratio: 1520/2175 (69.9%)  <-- CALCULATED!
  ROE: 1679/2175 (77.2%)
  Promoter Holding: 2137/2175 (98.3%)

[SUCCESS] ALL VALIDATIONS PASSED
```

---

### PHASE 5: Process stock_aliases (Manual - when ready)

**Step 1: Download BSE Excel**
1. Visit: https://www.bseindia.com/corporates/Comp_Name.aspx
2. Download company name changes (or copy table to Excel)
3. Save as: `data/bse_name_changes.xlsx`

**Expected Excel format:**
| Old Name | New Name | Date |
|----------|----------|------|
| 8K Miles Software Services Limited | SECUREKLOUD TECHNOLOGIES LIMITED | 2021-01-20 |
| A Infrastructure Limited | KANORIA ENERGY & INFRASTRUCTURE LIMITED | 2023-05-04 |

**Step 2: Process Excel**
```bash
python scripts/rebuild/04_process_stock_aliases.py
```

**What it does:**
- Reads BSE Excel
- Matches company names to NSE symbols (fuzzy matching)
- Generates `data/symbol_mappings.py` with all mappings

**Output:**
```
[INFO] Loading: data/bse_name_changes.xlsx
[OK] Loaded 250 name changes

[INFO] Matching BSE names to NSE symbols...
[MATCH] 8K Miles → SECUREKLOUD (Symbol: SECUREKLOUD)
[MATCH] A Infrastructure → KANORIA (Symbol: KANORIA)
...

[SUMMARY]
  Matched to NSE: 85
  Not found on NSE: 165 (BSE-only stocks)

[OK] Generated: data/symbol_mappings.py
[OK] Added 85 mappings
```

---

## EXPECTED RESULTS

### Database Size:
- Current: 1.6 GB, 7.1M records
- New: 3-5 GB, 10-15M records

### Data Quality:
| Metric | Current | New |
|--------|---------|-----|
| NULL company names | 67% (1,470) | 0% ✅ |
| Negative prices | 642 | 0 ✅ |
| PB Ratio coverage | 0% | 70%+ ✅ |
| Debt/Equity coverage | 0% | N/A* |
| Query performance | 0.23ms | < 1ms ✅ |

*Debt/Equity not found in Screener.in validation tests

### Coverage:
```
stocks_master: 2,182 stocks
daily_ohlc: ~10-15M records (20 years × 2,182 stocks)
fundamentals: 2,175 records (99.7% coverage)
corporate_actions: ~15,000 records
```

---

## TROUBLESHOOTING

### Error: "Database not found"
**Solution:** Run `01_create_schema.py` first

### Error: "openchart not found"
**Solution:** `pip install openchart`

### Error: "nselib not found"
**Solution:** `cd nselib-2.0 && pip install -e .`

### Error: "OpenChart: Data not downloaded"
**Solution:** OpenChart needs `.download()` first - script handles this automatically

### Warning: "Some fundamentals failed"
**Expected:** Some stocks are not on Screener.in (delisted, suspended)
**Action:** Continue - 95%+ coverage is normal

### Error: "Rate limiting / HTTP 429"
**Solution:** Script has delays (2s for Screener.in, 0.5s for OpenChart)
**If still occurs:** Increase delays in script

---

## FILES STRUCTURE

```
scripts/rebuild/
├── README.md                     <-- This file
├── 01_create_schema.py           <-- Create database schema
├── 02_master_rebuild.py          <-- Download all data
├── 03_validate_database.py       <-- Validate quality
└── 04_process_stock_aliases.py   <-- Process BSE Excel

database/
├── stock_market.db               <-- OLD database (backup)
└── stock_market_new.db           <-- NEW database (after rebuild)

logs/
├── 01_create_schema_*.log
├── 02_rebuild_*.log
└── 03_validate_*.log

data/
├── bse_name_changes.xlsx         <-- USER UPLOADS THIS
└── symbol_mappings.py            <-- AUTO-GENERATED
```

---

## SWITCHING TO NEW DATABASE

After validation passes:

```bash
# Windows
move database\stock_market.db database\stock_market_old.db
move database\stock_market_new.db database\stock_market.db

# Linux/Mac
mv database/stock_market.db database/stock_market_old.db
mv database/stock_market_new.db database/stock_market.db
```

**Update backend:**
```python
# No changes needed if using same DB_FILE path
# Backend will automatically use new database
```

---

## DAILY UPDATES (After Rebuild)

Create a daily update script:

```python
# scripts/daily_update.py

from nselib import capital_market
from datetime import datetime
import sqlite3

# Get yesterday's bhav copy
date_str = datetime.now().strftime('%d-%m-%Y')
bhav = capital_market.bhav_copy_equities(date_str)

# Update database
conn = sqlite3.connect('database/stock_market.db')
cursor = conn.cursor()

for _, row in bhav.iterrows():
    cursor.execute("""
        INSERT OR REPLACE INTO daily_ohlc
        (symbol, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (row['SYMBOL'], row['TIMESTAMP'], row['OPEN'],
          row['HIGH'], row['LOW'], row['CLOSE'], row['TOTTRDQTY']))

conn.commit()
conn.close()

print(f"Updated {len(bhav)} stocks")
```

**Schedule daily:**
```bash
# Linux/Mac crontab
0 18 * * * cd /path/to/project && python scripts/daily_update.py

# Windows Task Scheduler
# Run daily at 6 PM
```

---

## SUPPORT

**Documentation:**
- [FINAL_VALIDATED_SCRAPERS.md](../../FINAL_VALIDATED_SCRAPERS.md) - All APIs with test results
- [VALIDATION_COMPLETE_FINAL_REPORT.md](../../VALIDATION_COMPLETE_FINAL_REPORT.md) - What was wrong, what's correct
- [DATABASE_REBUILD_READY.md](../../DATABASE_REBUILD_READY.md) - Overview

**For Issues:**
- Check logs in `logs/` directory
- Review validation output
- Check `download_log` table in database

**Contact:**
- Review validation tests in [ACTUAL_DATA_SOURCE_VALIDATION.md](../../ACTUAL_DATA_SOURCE_VALIDATION.md)

---

**Status:** ✅ PRODUCTION READY
**Version:** 2.0
**Last Updated:** October 8, 2025
