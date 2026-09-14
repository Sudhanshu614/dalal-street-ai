# Database Enhancement Deployment Guide

## Quick Start (Single Command)

To run all enhancements in one go:

```bash
python scripts/rebuild/MASTER_DEPLOY.py
```

This will:
1. Add 21 new fields to fundamentals table
2. Scrape enhanced data for 2,103 stocks from Screener.in
3. Calculate returns from historical OHLC data
4. Import IPO data from 19 CSV files (2007-2025)

**Estimated Total Time:** ~2-3 hours

---

## What Gets Enhanced

### New Fields Added to `fundamentals` Table

**Industry/Sector Classification (4 fields)**
- `industry` - Industry category (e.g., "Automobiles", "Banks")
- `sector` - Sector category (e.g., "Consumer Discretionary", "Financials")
- `subsector` - Sub-sector category
- `business_segment` - Specific business segments

**Returns Data (6 fields)**
- `returns_1month` - 1-month return %
- `returns_3month` - 3-month return %
- `returns_6month` - 6-month return %
- `returns_1year` - 1-year return %
- `returns_3year` - 3-year return %
- `returns_5year` - 5-year return %

**Growth Metrics (8 fields - CAGR %)**
- `sales_growth_3year` - Sales CAGR 3 years %
- `sales_growth_5year` - Sales CAGR 5 years %
- `sales_growth_10year` - Sales CAGR 10 years %
- `profit_growth_3year` - Profit CAGR 3 years %
- `profit_growth_5year` - Profit CAGR 5 years %
- `profit_growth_10year` - Profit CAGR 10 years %
- `eps_growth_3year` - EPS CAGR 3 years %
- `eps_growth_5year` - EPS CAGR 5 years %

**Debt Metrics (2 fields)**
- `debt_to_equity` - Debt to Equity ratio (for non-banks)
- `total_deposits` - Total Deposits in Crores (for banks only)

### New Table: `ipo_data`

Contains IPO data for ~1,000-2,000 companies (2007-2025):
- `company_name` - Company name from CSV
- `symbol` - Mapped stock symbol (if found)
- `listing_date` - IPO listing date
- `issue_price` - Issue price (Rs)
- `listing_day_close` - Listing day close price
- `listing_day_gain_pct` - Listing day gain/loss %
- `current_price_bse` - Current price at BSE
- `current_price_nse` - Current price at NSE
- `current_gain_pct` - Current gain/loss from issue price %
- `symbol_mapped` - Whether symbol was successfully mapped (1=yes, 0=no)

---

## Deployment Options

### Option 1: Run Everything (Recommended)
```bash
python scripts/rebuild/MASTER_DEPLOY.py
```

### Option 2: Run Specific Phases Only
```bash
# Only migration and scraping
python scripts/rebuild/MASTER_DEPLOY.py --phases 7,8

# Only IPO import
python scripts/rebuild/MASTER_DEPLOY.py --phases 11
```

### Option 3: Skip a Phase
```bash
# Skip scraping (if you want to do it later)
python scripts/rebuild/MASTER_DEPLOY.py --skip 8
```

### Option 4: Resume from a Phase
```bash
# Resume from phase 9 (if phase 7 and 8 are already done)
python scripts/rebuild/MASTER_DEPLOY.py --resume 9
```

### Option 5: Dry Run (Preview)
```bash
# See what would be done without executing
python scripts/rebuild/MASTER_DEPLOY.py --dry-run
```

---

## Individual Phase Scripts

If you need to run phases individually:

### Phase 7: Add Enhanced Fields (< 1 minute)
```bash
python scripts/rebuild/07_migrate_add_enhanced_fields.py
```

### Phase 8: Scrape Enhanced Fundamentals (~2 hours)
```bash
python scripts/rebuild/08_scrape_enhanced_fundamentals.py
```
- Scrapes 2,103 stocks from Screener.in
- 2 seconds delay per stock (rate limiting)
- Extracts: Industry, Growth metrics, Debt/Equity, Bank deposits

### Phase 9: Calculate Returns (~10 minutes)
```bash
python scripts/rebuild/09_calculate_returns.py
```
- Calculates returns from existing daily_ohlc table
- 6 return periods: 1M, 3M, 6M, 1Y, 3Y, 5Y

### Phase 11: Import IPO Data (~5 minutes)
```bash
python scripts/rebuild/11_import_ipo_data.py
```
- Imports 19 CSV files from IPO_DATA folder
- Maps company names to symbols using 3-layer fuzzy matching
- Creates new `ipo_data` table

---

## Timeline Breakdown

| Phase | Name | Time | Description |
|-------|------|------|-------------|
| 7 | Migration | < 1 min | Add 21 new columns to fundamentals |
| 8 | Scraping | ~2 hours | Scrape 2,103 stocks from Screener.in |
| 9 | Calculate | ~10 min | Calculate returns from OHLC data |
| 11 | IPO Import | ~5 min | Import 19 CSV files |
| **Total** | | **~2-3 hours** | |

---

## Resuming After Interruption

If the deployment is interrupted:

1. **Check which phase was running:**
   ```bash
   # Check the log file
   tail logs/MASTER_DEPLOY_*.log
   ```

2. **Resume from that phase:**
   ```bash
   # If phase 8 failed, resume from 8
   python scripts/rebuild/MASTER_DEPLOY.py --resume 8
   ```

3. **Or run the remaining phases individually**

---

## Monitoring Progress

### Real-time Monitoring
```bash
# Watch the latest log file
tail -f logs/MASTER_DEPLOY_*.log

# Or for individual phases
tail -f logs/08_enhanced_fundamentals_*.log
```

### Check Database Status

After deployment, check what was populated:

```sql
-- Check how many stocks have industry data
SELECT COUNT(*) FROM fundamentals WHERE industry IS NOT NULL;

-- Check how many stocks have growth data
SELECT COUNT(*) FROM fundamentals WHERE sales_growth_3year IS NOT NULL;

-- Check how many stocks have debt/equity
SELECT COUNT(*) FROM fundamentals WHERE debt_to_equity IS NOT NULL;

-- Check how many banks have deposits
SELECT COUNT(*) FROM fundamentals WHERE total_deposits IS NOT NULL;

-- Check how many stocks have returns data
SELECT COUNT(*) FROM fundamentals WHERE returns_1year IS NOT NULL;

-- Check IPO data
SELECT COUNT(*) FROM ipo_data;
SELECT COUNT(*) FROM ipo_data WHERE symbol_mapped = 1;
```

---

## Expected Results

After successful deployment:

| Field Category | Expected Coverage |
|----------------|-------------------|
| Industry/Sector | ~95% (2,000+ stocks) |
| Growth Metrics | ~85% (1,800+ stocks) |
| Debt/Equity | ~70% (1,500+ stocks) |
| Bank Deposits | ~50 banks |
| Returns Data | ~100% (all stocks with OHLC data) |
| IPO Data | 1,000-2,000 IPOs imported |
| IPO Symbol Mapping | ~95% success rate |

---

## Troubleshooting

### Issue: Phase 8 takes too long
**Solution:** The scraping phase must respect rate limits (2 sec/stock). This is intentional to avoid being blocked by Screener.in. You can:
- Run it overnight
- Or split it into batches by modifying the script

### Issue: Some IPO symbols not mapped
**Expected:** ~5% of IPO company names won't map to symbols (delisted, name changes, etc.)
**Check:** Query `ipo_data WHERE symbol_mapped = 0` to see unmapped entries

### Issue: Bank deposits not showing
**Check:** Only companies with "Deposits" in balance sheet will have this field populated. Regular companies won't have this field.

### Issue: Phase fails with HTTP 429 (Too Many Requests)
**Solution:** Increase the delay in Phase 8 script:
```python
# In 08_scrape_enhanced_fundamentals.py, line 346
time.sleep(5)  # Change from 2 to 5 seconds
```

---

## Database Location

**IMPORTANT:** All enhancements update `stock_market_new.db` only.

The original `stock_market.db` remains unchanged.

---

## Post-Deployment

After successful deployment:

1. **Test the new fields in your chatbot:**
   - "Show me all banks with their deposits"
   - "Find stocks with sales growth > 15% for 3 years"
   - "Compare RELIANCE and TCS growth metrics"
   - "Show me IPOs from 2024"

2. **Update your AI prompts** to include new fields in queries

3. **Add new intents** in `intent_classifier.py` for:
   - IPO queries ("Show recent IPOs")
   - Growth screening ("Find high growth stocks")
   - Industry filtering ("Show all automobile stocks")

---

## Next Steps

1. Run the master deployment script
2. Monitor the logs
3. Verify results in database
4. Update backend to use new fields
5. Test chatbot with enhanced queries

---

## Support

If you encounter issues:
1. Check the log files in `logs/` directory
2. Run individual phase scripts to isolate the problem
3. Check database schema: `sqlite3 database/stock_market_new.db ".schema"`
