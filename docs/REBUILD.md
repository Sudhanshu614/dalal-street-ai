# Building the database

The repository ships no database. This document is how you build one.

Two paths:

- **Sample** — schema plus a few thousand rows, enough to boot the app and develop against. Minutes.
- **Full warehouse** — 30 years of OHLCV, fundamentals, corporate actions, indices, ETFs. Hours to days, depending on how much history you backfill.

Read [`DATA.md`](DATA.md) first. The data you are about to collect carries obligations that this repository's licence does not remove.

---

## Path A — sample database

```bash
python scripts/bootstrap_db.py --sample
```

Creates `App/database/stock_market_new.db` with the full schema and a small slice of recent data pulled live from NSE. Enough to exercise the ticker resolver, the query builder, and the frontend. Not enough for historical analysis.

Schema only, no rows:

```bash
python scripts/bootstrap_db.py --schema-only
```

---

## Path B — full warehouse

### Step 0 — manual downloads

Five files must be downloaded by hand from NSE. They are not fetchable programmatically without tripping NSE's bot protection. Place all of them in `App/database/`.

| File | Source page | Which link |
|---|---|---|
| `stock_master.csv` | [Securities available for trading](https://www.nseindia.com/static/market-data/securities-available-for-trading) | *Securities available for Equity segment (.csv)* |
| `namechange.csv` | same page | *Changes in Company Names (.csv)* |
| `symbolchange.csv` | same page | *Changes in Symbols (.csv)* |
| `CF-CA-equities-*.csv` | [Corporate filings — actions](https://www.nseindia.com/companies-listing/corporate-filings-actions) | Set range `01-01-1980` → today, export CSV |
| `IPO-PastIssue-*.csv` | [All upcoming issues / IPO](https://www.nseindia.com/market-data/all-upcoming-issues-ipo) | Set range `01-01-1980` → today, export CSV |

The `CF-CA-equities-*` and `IPO-PastIssue-*` filenames embed their date range. The pipeline auto-discovers the newest matching file, so you do not need to rename them.

### Step 1 — schema

```bash
python App/scriptsrebuild/01_create_schema.py
```

### Step 2 — ticker resolution tables

Everything the resolver depends on: master list, name changes, symbol changes, corporate actions.

```bash
python App/scriptsrebuild/04_daily_nse_update.py
python App/scriptsrebuild/05_ingest_name_changes_excel.py    # optional, if you have the NSE XLSX
python App/scriptsrebuild/11_import_ipo_data.py
```

Verify before continuing — everything downstream depends on this being right:

```bash
python -c "from App.src.data_fetcher.ticker_resolver import TickerResolver; \
r = TickerResolver(); print(r.resolve_any('TATA MOTORS'))"
```

### Step 3 — historical OHLCV

The long one. Backfills `daily_ohlc` from OpenChart.

```bash
python App/scriptsrebuild/02_master_rebuild.py
python App/scriptsrebuild/06_rescrape_failed_stocks.py       # retry the failures
```

### Step 4 — fundamentals

> **Licensing note:** this step scrapes screener.in. Fine for personal and educational use; do not redistribute the result. See [`DATA.md`](DATA.md).

```bash
python App/scriptsrebuild/10_sync_new_companies.py
python App/scripts/rebuild/unified_data_updater.py --all
python App/scriptsrebuild/09_calculate_returns.py
```

Granular alternatives to `--all`:

| Flag | Scrapes |
|---|---|
| `--basic` | Price, PE, market cap, holdings, ratios |
| `--enhanced` | Industry, sector, growth rates, debt |
| `--quarterly` | Quarterly results |
| `--annual` | Annual financials |
| `--all --start-from SYMBOL` | Resume an interrupted run |

### Step 5 — indices and ETFs

Slow. Expect hours.

```bash
python App/scripts/rebuild/11_scrape_all_indices_openchart.py
python App/scripts/rebuild/10_daily_update_indices_etfs.py --start-date 1995-01-01 --end-date $(date +%F)
```

### Step 6 — FII/DII flows

```bash
python App/scriptsrebuild/14_scrape_fii_dii_data.py
```

---

## Daily update

Once the warehouse exists, this is the incremental run. Refresh the five manual CSVs first if there have been corporate actions.

```bash
python App/scriptsrebuild/04_daily_nse_update.py            # master + name/symbol changes + CA
python App/scriptsrebuild/11_import_ipo_data.py             # IPOs
python App/DAILY_RUNNER.py                                  # today's OHLCV
python App/scriptsrebuild/10_sync_new_companies.py          # pick up new listings
python App/scripts/rebuild/unified_data_updater.py --all    # fundamentals
python App/scriptsrebuild/09_calculate_returns.py           # derived returns
python App/scripts/rebuild/10_daily_update_indices_etfs.py --today
python App/scriptsrebuild/14_scrape_fii_dii_data.py --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD>
```

Backfill a gap:

```bash
python App/DAILY_RUNNER.py --start 2026-02-17 --end 2026-02-25
python App/DAILY_RUNNER.py --date 2026-02-04 --force        # force re-fetch one day
```

---

## Verifying a build

```bash
python scripts/verify_db.py
```

Checks row counts per table, the `daily_ohlc` date range for holes, orphaned symbols in `daily_ohlc` that are absent from `stocks_master`, and runs the ticker resolver against a fixture set of known renames.

Manual spot-check:

```sql
SELECT MIN(date), MAX(date), COUNT(DISTINCT symbol), COUNT(*) FROM daily_ohlc;
SELECT COUNT(*) FROM name_change_events;
SELECT COUNT(*) FROM symbol_change_events;
```

For reference, a complete build as of early 2026 produced ~10.2 M `daily_ohlc` rows across 5,261 symbols spanning 1995-02-08 to present.

---

## Notes and gotchas

- **NSE rate-limits aggressively.** The reliability layer (`App/src/reliability/`) throttles to 5 req/s for jugaad-data with exponential backoff. Do not raise it.
- **`scriptsrebuild/` and `scripts/rebuild/` overlap.** Historical accident — two generations of the pipeline. `scripts/rebuild/unified_data_updater.py` is the newer, more capable fundamentals path. Consolidating these is a tracked issue.
- **WAL mode is on.** You will see `.db-wal` and `.db-shm` alongside the database. Do not copy the `.db` without checkpointing first, or you will copy a stale snapshot.
- **TA-Lib needs system libraries.** On Debian/Ubuntu, build from source or use the Dockerfile. `pandas-ta` is the automatic fallback if TA-Lib is unavailable.
