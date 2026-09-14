# Complete file inventory

Every file and folder in this repository, what it does, and whether it should go public.

> **Status: cleanup complete.** Everything marked ❌ below has been moved to `.private/archive/`
> (62 files) and is no longer in the repository body. Everything marked ✅ is present and tracked.
> New since this was first written: `scripts/` (bootstrap, verify, export, benchmark),
> `tests/` (165 tests), `.github/` (CI + issue forms), `docs/KAGGLE.md`, `docker-compose.yml`.
> The descriptions below remain accurate for every surviving file.

**Legend**

| Mark | Meaning |
|---|---|
| ✅ | Publish — safe and useful |
| ⚠️ | Publish, but needs a cleanup pass first |
| ❌ | Do not publish — gitignored or quarantined |
| 🤔 | Your call — see the note |

**Scale:** 4,843 files on disk, but only **172 are code, config, or docs**. The rest are cached CSVs, log files, and a 3.9 GB database — all transient or excluded.

---

## 1. Root level

### Documentation (all new, written for the release)

| File | Lines | What it is | |
|---|---:|---|:--:|
| `README.md` | 173 | Project intro, architecture diagram, the 7-tier resolver table, quickstart, config reference | ✅ |
| `CONTRIBUTING.md` | 53 | Setup instructions, ranked list of where help is needed, ground rules (no secrets, no absolute paths, no hardcoded schema) | ✅ |
| `SECURITY.md` | 23 | Vulnerability reporting, and a deployment-safety checklist (admin token, CORS, reverse proxy) | ✅ |
| `LICENSE` | 202 | Apache License 2.0, copyright Sudhanshu Bawane | ✅ |

### Configuration

| File | What it is | |
|---|---|:--:|
| `.env.example` | Template for every environment variable, with comments. Users copy this to `.env`. Contains no real values | ✅ |
| `.gitignore` | 97 lines. Excludes secrets, databases, CSVs, caches, logs, `.private/`. **Needs revision** — currently blanket-ignores `test_*.py`, `check_*.py`, `analyze_*.py`, `*.json`, `*.txt`, which is why 90+ pipeline scripts are untracked | ⚠️ |
| `.gitattributes` | 5 lines. Marks `*.db` as binary. Should also add `* text=auto eol=lf` — the repo currently has CRLF line endings throughout, which makes every diff look like a full rewrite | ⚠️ |
| `requirements.txt` | 5 lines. Frontend deps: streamlit, requests, pandas, pandas-ta, TA-Lib | ✅ |

### Entry point

| File | What it is | |
|---|---|:--:|
| `streamlit_app.py` | 21-line shim. Exists only so Streamlit Cloud can find the app — adds `App/frontend` to the path, `chdir`s into it, imports and runs `main()` | ✅ |

### Deployment scripts

| File | What it does | |
|---|---|:--:|
| `upload_database_to_cloud.ps1` | PowerShell. Uploads the SQLite database to Google Cloud Storage. Now reads `GCS_BUCKET` and project root from environment instead of hardcoded values | ⚠️ |
| `download_database.sh` | Bash, runs on the server. Pulls the database from GCS and restarts the backend service. Now reads `GCS_BUCKET`, `GCE_INSTANCE`, `GCE_ZONE` from environment | ⚠️ |
| `download_database_fixed.sh` | 28-line variant of the above. Duplicate — **delete one of these** | 🤔 |
| `complete_database_update.ps1` | 197 lines. Orchestrates the full daily update: runs all pipeline scripts in sequence, then uploads. Windows-only | ⚠️ |

> These four assume Google Cloud. They are useful as a reference deployment but will not work for anyone who is not on GCP. Consider moving them to `deploy/` with a README explaining they are one specific deployment, not the only one.

### Loose scripts at root — all leftover debugging

| File | What it is | |
|---|---|:--:|
| `test_grounding.py` | Gemini Search-grounding experiment | ❌ |
| `test_grounding_correct.py` | Same experiment, attempt 2 | ❌ |
| `test_grounding_final.py` | Same experiment, attempt 3 | ❌ |
| `test_grounding_official.py` | Same, using Google's documented syntax | ❌ |
| `test_grounding_real.py` | Same, loading `.env` the way the server does | ❌ |
| `test_official_grounding.py` | Same, verbatim from the Gemini docs | ❌ |
| `test_actual_system.py` | Probes the live `ChatResponse` model and chat endpoint | ❌ |
| `test_resolver_local.py` | 23-line resolver smoke test. **Has a syntax error** — does not parse | ❌ |
| `verify_annual.py` | Checks `annual_financials` after an update | ❌ |
| `verify_update.py` | Checks row counts after a daily run | ❌ |
| `extract_famous_changes.py` | Pulls well-known name changes out of the DB for demo purposes | 🤔 |
| `annual`, `quartely` | Extensionless 16 KB text dumps of financial data. `quartely` is a typo | ❌ |
| `0` | Empty 0-byte file, created by a mistyped shell redirect | ❌ |

> Six near-identical grounding experiments is the archaeology of a feature that did not land. The conclusion is written up in `docs/notes/`. Delete the scripts.

---

## 2. `App/` — application root

### The core library: `App/src/`

This is the part worth open-sourcing.

**`App/src/data_fetcher/`**

| File | Lines | What it does | |
|---|---:|---|:--:|
| `ticker_resolver.py` | 880 | **The headline module.** Resolves any user string to a live NSE symbol through a 7-tier confidence-scored cascade: direct match → ETF match → recursive symbol-change chase → fuzzy match → index aliases → name-change lookup → delisting check → demerger-child correlation → not-found with suggestions | ✅ |
| `universal_data_fetcher.py` | 1,816 | One `fetch(query_type, params)` entry point instead of per-stock functions. Discovers the SQLite schema at startup via `PRAGMA`, routes each query type through primary → backup → fallback sources with measured timeouts, and implements the high-level tools (`query_stocks`, `calculate_indicators`, `query_corporate_actions`, `fetch_stock_data`) | ✅ |
| `bhavcopy_downloader.py` | 1,337 | Downloads and ingests NSE daily bhavcopy. Primary source NSE Archives CSV, falls back to jugaad-data then nselib. Tracks which tickers appear and disappear, which is how symbol changes get detected | ✅ |
| `generic_query_builder.py` | 220 | Builds parameterised SQL from `(table, filters, fields, sort, limit)`. Validates every identifier against the runtime-discovered schema; all values go through `?` placeholders. The cleanest module in the repo | ✅ |
| `__init__.py` | 9 | Package marker | ✅ |
| `reproduce_bug.py` | 60 | Debug script simulating a fetcher call. Currently git-tracked, should not be | ❌ |
| `reproduce_issue.py` | 84 | Same. Was the file with a hardcoded `E:\` path (now fixed), but still does not belong in the package | ❌ |

**`App/src/llm/`**

| File | Lines | What it does | |
|---|---:|---|:--:|
| `function_declarations.py` | 866 | Defines the tools Gemini can call (`resolve_ticker`, `fetch_any`, `query_stocks`, `calculate_indicators`, `query_corporate_actions`, `fetch_stock_data`) and builds the system prompt. **Now introspects the live database via `PRAGMA` at startup** instead of shipping a hand-typed schema that had drifted badly out of date | ✅ |
| `providers.py` | 384 | `LLMProvider` abstraction with Gemini / Groq / Hybrid implementations. **Dead code** — nothing imports it, so `LLM_PROVIDER` has no effect. Now carries an explicit "NOT WIRED" warning in its docstring | ⚠️ |
| `natural_language_interface.py` | 272 | Higher-level NL wrapper around the providers. Also dead, also now marked | ⚠️ |
| `__init__.py` | 9 | Package marker | ✅ |
| `natural_language_interface.py.gemini_backup` | 272 | A backup file that got committed. Pure junk | ❌ |

**`App/src/reliability/`** — genuinely well-factored, all ✅

| File | Lines | What it does |
|---|---:|---|
| `reliability_config.py` | 303 | All tunables as data: rate limits, retry configs, circuit-breaker thresholds, per data source. SQLite 1000 req/s; jugaad-data 5 req/s burst 10 |
| `circuit_breaker.py` | 325 | Three-state breaker (closed / open / half-open) so a failing source stops getting hammered |
| `retry_policy.py` | 255 | Exponential backoff with jitter |
| `rate_limiter.py` | 207 | Token-bucket limiter |
| `__init__.py` | 21 | Package exports |

**`App/src/lifecycle/`**

| File | Lines | What it does | |
|---|---:|---|:--:|
| `lifecycle_manager.py` | 380 | Company lifecycle tracking (listing → renames → delisting) against a separate `company_lifecycle.db`. **Orphaned** — nothing imports it, and it was never git-tracked. Was the worst offender for hardcoded paths (5 of them, now fixed) | 🤔 |
| `schema.sql` | 132 | Schema for that separate lifecycle database | 🤔 |

> Either wire this in or delete it. Right now it is a fully-built subsystem that nothing calls.

### `App/api/` — the backend

| File | Lines | What it does | |
|---|---:|---|:--:|
| `server.py` | 1,503 | FastAPI backend. The Gemini function-calling loop (max 5 turns), pre-tool ticker resolution with a confidence floor, LLM self-validation with auto-retry, hallucination monitoring, tab-table contract enforcement, and the `/admin/update/*` endpoints. **Now hardened**: admin auth, configurable CORS, no hardcoded model or paths, no debug-reload default | ✅ |
| `requirements.txt` | 60 | Backend deps, heavily commented with install-order warnings (numpy before scipy, `pandas_market_calendars` before nselib). jugaad-data installs from GitHub, not PyPI | ✅ |
| `Dockerfile` | 56 | python:3.11-slim, builds TA-Lib from source. **Now fetches over HTTPS with a verified SHA-256 checksum** (was plain HTTP) | ✅ |
| `test_option_chain_call.py` | 39 | Ad-hoc option-chain probe | ❌ |

### `App/frontend/` — the UI

| File | Lines | What it does | |
|---|---:|---|:--:|
| `streamlit_app.py` | 451 | The Streamlit chat interface. Health-checks the backend, renders suggestion pills, POSTs to `/api/chat` with a 150 s timeout. Personal attribution in the footer has been genericised | ✅ |
| `universal_renderer.py` | 404 | Renders arbitrary response structures without knowing field names ahead of time. Handles acronym preservation, snake_case → Title Case | ✅ |
| `display_components.py` | 386 | Detects data shape then picks a strategy: single-stock card, comparison table, time-series chart, corporate-actions list, or generic fallback | ✅ |
| `formatters.py` | 297 | Formats by inferred type rather than field name — currency, percentage, large numbers, dates | ✅ |
| `Dockerfile` | 23 | Streamlit container on port 8501 | ✅ |

### `App/` root — operational scripts

| File | Lines | What it does | |
|---|---:|---|:--:|
| `config.py` | 100 | **Rewritten.** Central configuration, everything from environment variables, paths resolved from `PROJECT_ROOT`. Loads `.env`. Exposes `DB_PATH`, `CORS_ORIGINS`, `ADMIN_TOKEN`, `GEMINI_MODEL`, and a `require_gemini_key()` helper | ✅ |
| `DAILY_RUNNER.py` | 94 | The standard daily OHLC update with full ticker tracking. Supports `--date`, `--start`/`--end`, `--force` | ✅ |
| `backfill_be_series.py` | 118 | Reprocesses history to add BE (Book Entry) series alongside EQ | ✅ |
| `BACKFILL_BE_SERIES_FULL.py` | 131 | Same, `INSERT OR IGNORE` strategy so existing EQ rows survive | 🤔 |
| `BACKFILL_BE_SERIES_FORCE.py` | 71 | Same, `force=True` over all 6,576 dates from 1995 | 🤔 |
| `BACKFILL_CUSTOM.py` | 86 | Date-range backfill with ticker tracking disabled for speed | 🤔 |
| `STEP1_DELETE_ALL_OHLC.py` | 52 | **Destructive.** Truncates `daily_ohlc` | ⚠️ |
| `STEP2_BACKFILL_ALL_CLEAN.py` | 64 | Full rebuild after the above, skipping duplicate checks | ⚠️ |
| `check_data_range.py` | 35 | Prints the OHLC date range | ❌ |
| `test_be_series.py` | 65 | Checks BE loading works | ❌ |
| `test_backfill_dry_run.py` | 61 | 3-date dry run of the backfill logic | ❌ |
| `.env` | 5 | **Your real API keys.** Gitignored, backed up to `.private/` | ❌ |

> Four overlapping BE-series backfill scripts is three too many. Consolidate into one with flags before publishing, and put a confirmation prompt on the two destructive `STEP*` scripts.

### `App/tests/`

| File | Lines | What it does | |
|---|---:|---|:--:|
| `ui_playwright_test.py` | 159 | The only real test in the repo. Playwright browser test of the chat UI. **Cannot run for anyone** — `playwright` is in no requirements file, and `.gitignore` excludes `App/tests/` entirely | ⚠️ |

---

## 3. `App/scriptsrebuild/` — the ingestion pipeline (first generation)

45 files. Numbered phase scripts that build the database from scratch. **Currently untracked by git** — this is the single biggest gap, because without these nobody can build a database.

**Schema and setup**

| File | What it does |
|---|---|
| `01_create_schema.py` | Creates the full schema with CHECK, FOREIGN KEY, and UNIQUE constraints |
| `01_drop_old_tables.py` | Drops the legacy BSE-contaminated tables (including the old 45,059-row `stock_aliases`) |
| `02_create_new_tables.py` | Creates the four NSE-authoritative tables: `name_change_events`, `symbol_change_events`, `corporate_events`, `delisting_events` |
| `00_full_refresh.py` | Wrapper that runs a full refresh |

**Data loading**

| File | What it does |
|---|---|
| `02_master_rebuild.py` | The big one. Stock master from nselib, 20+ years of OHLCV from OpenChart, fundamentals from Screener.in |
| `03_load_nse_data.py` | Loads `namechange.csv`, `symbolchange.csv`, and the CF-CA corporate-actions CSV |
| `03_validate_database.py` | Quality checks: NULL company names, negative prices, symbol-as-name bugs, coverage stats |
| `04_daily_nse_update.py` | The daily incremental — new listings, name changes, symbol changes, corporate actions |
| `06_ingest_stock_master_csv.py` | Ingests the manually-downloaded NSE master CSV |

**Ticker-resolution data** (feeds the resolver)

| File | What it does |
|---|---|
| `04_process_stock_aliases.py` | Legacy: processed 27 years of BSE name-change CSVs. Superseded |
| `05_ingest_name_changes_excel.py` | Ingests NSE's name-change XLSX |
| `07_snapshot_and_alias_events.py` | Snapshots master state and derives alias events |
| `08_map_excel_alias_events.py` | Maps Excel alias rows onto canonical events |
| `09_migrate_name_changes_raw.py` | Schema migration for raw name-change storage |
| `12_build_company_names_canonical.py` | Builds the canonical company-name mapping |
| `13_backfill_company_names.py` | Backfills names across tables |
| `14_mismatch_monitor_alias_vs_canonical.py` | Reports where aliases and canonical names disagree |
| `15_update_fundamentals_company_names.py` | Syncs names into `fundamentals` |
| `16_sync_company_names_all_tables.py` | Syncs names across every table |
| `97_isin_enrichment_aggregator.py` | Aggregates ISIN data |
| `98_isin_alias_populate.py` | Populates aliases from ISIN matches |

**Fundamentals** (⚠️ all scrape screener.in — see `DATA.md`)

| File | What it does |
|---|---|
| `scrape_fundamentals_database.py` | Current-snapshot scrape, 18 fields per company |
| `update_fundamentals.py` | Unified scraper — one Screener.in fetch gets basic + enhanced |
| `08_scrape_enhanced_fundamentals.py` | Industry/sector classification, growth rates, debt |
| `06_rescrape_failed_stocks.py` | Retries the ~80 companies that failed on the negative-book-value constraint |
| `05_migrate_book_value_constraint.py` | Removes that CHECK constraint (distressed companies legitimately have negative book value) |
| `07_migrate_add_enhanced_fields.py` | Adds enhanced columns; creates `quarterly_results` and `annual_financials` |
| `09_calculate_returns.py` | Computes 1M/3M/6M/1Y/3Y/5Y returns from `daily_ohlc` |
| `10_sync_new_companies.py` | Inserts placeholder rows for newly-listed companies |

**Market data**

| File | What it does |
|---|---|
| `10_scrape_market_indices.py` | Index history from NSE Archives `ind_close_all_DDMMYYYY.csv` |
| `10_scrape_ipo_data.py` | IPO data from Screener.in |
| `10_scrape_ipo_data_chittorgarh.py` | IPO data from Chittorgarh, 2010–2025 |
| `11_import_ipo_data.py` | Imports IPO CSV into `ipo_data` |
| `12_refresh_live_price_fields.py` | Refreshes live-price columns |
| `14_scrape_fii_dii_data.py` | FII/DII flows from NSE Archives |
| `15_scrape_bulk_deals.py` | Bulk deals from NSE. **Targets a table that was dropped** — dead |

**Orchestrators**

| File | What it does |
|---|---|
| `RUN_COMPLETE_REBUILD.py` | One command, full rebuild: deps → backup → schema → download (2-3 hrs) → validate |
| `MASTER_DEPLOY.py` | Runs phases 5–9 on a remote VM |
| `MASTER_ENHANCE_DATABASE.py` | Runs the enhancement phases in order |
| `MASTER_DAILY_JOBS.py` | Daily job wrapper |
| `AUTHORITATIVE_DAILY_RUNNER.py` | Another daily runner. Overlaps with `App/DAILY_RUNNER.py` |
| `99_query_debug.py` | Ad-hoc query debugging |

**Docs**

| File | What it is | |
|---|---|:--:|
| `README.md` | 358-line rebuild guide. **Paths point at the wrong directory** (`scripts/rebuild/` instead of `scriptsrebuild/`) | ⚠️ |
| `README_DEPLOYMENT.md` | 290-line deployment notes | ⚠️ |

---

## 4. `App/scripts/` — second-generation pipeline plus dev scripts

**`App/scripts/rebuild/`** — the newer, better pipeline. Overlaps confusingly with `scriptsrebuild/`.

| File | Lines | What it does | |
|---|---:|---|:--:|
| `unified_data_updater.py` | 812 | The current best fundamentals path. One scrape gets everything; ~50% faster. Flags: `--basic`, `--enhanced`, `--quarterly`, `--annual`, `--all`, `--start-from` | ✅ |
| `10_daily_update_indices_etfs.py` | 936 | Daily indices + ETFs. Discovers everything dynamically from OpenChart, writes to both `market_indices` and `market_etfs` | ✅ |
| `update_financial_results.py` | 605 | Quarterly and annual financials from Screener.in. Incremental — only new and stale companies | ✅ |
| `10_daily_update_nse.py` | 367 | Lightweight alternative using one NSE Archives CSV for all indices. Duplicates the above | 🤔 |
| `11_scrape_all_indices_openchart.py` | 255 | Full index history from OpenChart, no hardcoded index list | ✅ |
| `12_split_indices_and_etfs.py` | 76 | One-off migration that split the combined table into indices and ETFs | 🤔 |
| `cleanup_db_casing.py` | 48 | One-off fix for `'NIFTY 50'` vs `'Nifty 50'` inconsistency | 🤔 |

**`App/scripts/` root** — 33 ad-hoc development scripts, none of them real tests

Test/probe scripts (all ❌): `test_actual_live_prices.py`, `test_e2e_live_quote.py`, `test_enhanced_live_quote.py`, `test_etf_fuzzy.py`, `test_etf_normalize.py`, `test_fetcher_trace.py`, `test_final_verification.py`, `test_indicators_correct.py`, `test_indicators_current.py`, `test_jugaad_direct.py`, `test_live_capabilities.py`, `test_live_detail.py`, `test_live_market_hours.py`, `test_simple_e2e.py`, `test_with_file.py`, `frontend_backend_test.py`, `indicator_stress_test.py`, `frontend_indicator_stress_test.py`

Inspection scripts (all ❌): `analyze_etf_patterns.py`, `analyze_etf_structure.py`, `analyze_commit_diff.py`, `inspect_etf_table.py`, `inspect_jugaad_methods.py`, `probe_jugaad.py`, `quick_diag.py`, `quick_etf_test.py`, `check_database_updates.py`, `capture_output.py`, `capture_market_test.py`, `evaluate_data_sources.py` (0 bytes, empty)

Output artefacts (all ❌): `commit_diff_analysis.txt`, `db_check_output.txt`, `final_test_output.txt`, `test_market_output.txt`, `test_output.txt`, `test_result.txt`, `frontend_indicator_results.json`, `indicator_stress_results.json`, `test_queries_results.json`

`download_market_indices.py` (268 lines) pulls indices from Yahoo Finance — a source nothing else uses. 🤔

> These 33 files are the sediment of a year's debugging. A few contain useful assertions that could become real pytest cases. The rest should go.

---

## 5. `App/database/` — the data

| File | Size | What it is | |
|---|---:|---|:--:|
| `stock_market_new.db` | **3.9 GB** | The warehouse. 16 tables, 10.2 M OHLC rows across 5,261 symbols, 1995-02-08 → 2026-02-25 | ❌ |
| `stock_market_new.db-shm` | 32 KB | SQLite shared-memory file (WAL mode) | ❌ |
| `stock_market_new.db-wal` | 0 B | SQLite write-ahead log | ❌ |

Gitignored for two reasons: size, and the fact that NSE and screener.in data cannot be redistributed. See [`DATA.md`](DATA.md).

The six input CSV/XLSX files the pipeline needs (`stock_master.csv`, `namechange.csv`, `symbolchange.csv`, `CF-CA-equities-*.csv`, `IPO-PastIssue-*.csv`, `Company_Name_Changes_NSE.xlsx`) are also gitignored and **not currently on disk** — users download them from NSE per [`REBUILD.md`](REBUILD.md).

---

## 6. `docs/`

| File | Lines | What it is | |
|---|---:|---|:--:|
| `DATA.md` | 85 | Source provenance per table, the NSE and screener.in licensing constraints, what you may and may not redistribute, and what a *defensible* public dataset would look like instead | ✅ |
| `REBUILD.md` | 164 | How to build the database — sample path and full path, the five manual NSE downloads, phase-by-phase commands, daily update sequence, verification | ✅ |
| `ROADMAP.md` | 45 | Known bugs, what's missing, what's planned, and the design decisions worth knowing before you change something | ✅ |
| `FILE_INVENTORY.md` | — | This document | ✅ |
| `notes/GROUNDING_ANALYSIS.md` | 68 | Analysis of whether Google Search grounding would fit the current chat flow | ✅ |
| `notes/GROUNDING_IMPLEMENTATION_GUIDE.md` | 270 | How it would be implemented with the new `google-genai` SDK | ✅ |
| `notes/GROUNDING_REALITY_CHECK.md` | 82 | The conclusion: the old and new Gemini SDKs are incompatible packages, so this needs a migration first | ✅ |

`doc/New Daily Flow Runbook.md` (84 lines) still exists from before — superseded by `docs/REBUILD.md`. Delete it and the now near-empty `doc/` folder. ⚠️

---

## 7. Infrastructure and IDE config

| Path | What it is | |
|---|---|:--:|
| `.devcontainer/devcontainer.json` | GitHub Codespaces config. Python 3.11 image, auto-runs Streamlit on port 8501, opens `README.md` on attach (which now actually exists) | ✅ |
| `.streamlit/config.toml` | Streamlit theme — light base, blue `#1E88E5` primary | ✅ |
| `.trae/documents/` | **85 markdown files, 408 KB.** AI-agent task specs from the Trae IDE, each an imperative work order ("Migrate Ticker Resolution To Excel Source", "Universal Frontend Renderer"). This is your development history with an AI assistant, not user documentation. Untracked | 🤔 |
| `Project_Blueprint/` | **Empty folder.** Delete | ❌ |
| `Scripts/query_index_price.py` | 25-line index-price query. Note the capital `S` — a second directory that collides with `App/scripts` on case-insensitive filesystems. **Had a case bug** (`App/Database/`) | ❌ |
| `.venv/` | Your Python virtual environment. Never publish | ❌ |

> `.trae/documents/` is the interesting judgement call. 85 specs showing how the system was designed and redesigned is genuinely unusual material — few projects publish that. It also contains rough working notes. If you want to publish it, it needs a read-through first.

---

## 8. Transient data — none of it publishable

| Path | Files | Size | What it is | |
|---|---:|---:|---|:--:|
| `App/cache/bhavcopy/` | 4,206 | 303 MB | Cached daily bhavcopy CSVs from NSE | ❌ |
| `cache/bhavcopy/` | 67 | 16 MB | A second bhavcopy cache at root | ❌ |
| `logs/` | 98 | 1.7 MB | Pipeline run logs, timestamped | ❌ |
| `App/logs/` | 63 | 592 KB | Backend logs | ❌ |
| `extra/` | 140 | 8.6 MB | Scratch directory: 101 one-off Python scripts (`analyze_*`, `check_*`, `debug_*`, `diagnose_*`), 18 output dumps, 5 markdown debugging transcripts, a `dalal-backend.service` systemd unit, and `company_lifecycle.db` (6.9 MB) | ❌ |

All already gitignored. `extra/` in particular is a graveyard — worth deleting from disk once you are sure nothing in it is needed.

---

## 9. `.private/` — quarantined during this cleanup

Created and gitignored. **Nothing here has ever been committed to git** — verified against full history. But all of it was on disk in plaintext, so treat the credentials as compromised and revoke them.

| File | What it contains | Action |
|---|---|---|
| `Config.md` | Two GitHub personal access tokens, a Gemini API key, VM IPs, GCS bucket name, service-account email and ID | **Revoke both PATs and the Gemini key** |
| `gcp-service-account.json` | A real GCP service-account private key for `dalal-street-uploader@…` | **Delete the key in GCP IAM** |
| `env.backup` | Copy of `App/.env` — live Gemini and Groq keys | **Revoke the Groq key** |
| `Bot Pending.md` | Your TODO list, plus a draft job-application message naming a specific person and referencing a previous rejection, plus SSH commands with your Unix username | Personal — keep private |
| `# A Stock Market bot.md` | Project evolution notes (useful, now summarised in the README) plus an unrelated critique of a competitor product | Personal |
| `Deployment and Update Runbook.md` | 483 KB raw AI-chat transcript with internal IPs and deploy logs. Was the largest file in the repo | Personal |
| `Checking Git Changes.md` | AI-chat transcript about a `git status`, leaks a local Windows path | Personal |

---

## 10. What actually goes public

| Category | Count | Verdict |
|---|---:|---|
| Core library (`App/src/`) | 16 files | ✅ The reason to open-source this |
| Backend + frontend | 9 files | ✅ |
| Ingestion pipeline | ~52 files | ✅ **but must be un-ignored first** |
| Documentation | 11 files | ✅ All new |
| Config and infra | 8 files | ✅ |
| Ad-hoc test/debug scripts | ~55 files | ❌ Delete |
| Data, caches, logs | ~4,600 files | ❌ Already excluded |
| Personal / credentials | 7 files | ❌ Quarantined in `.private/` |

**Roughly 96 files should be public. About 55 should be deleted. The rest is already excluded.**

The main outstanding job is the `.gitignore` rewrite: it currently excludes `test_*.py`, `check_*.py`, `analyze_*.py`, `*.json`, and `*.txt` by pattern, which is why the pipeline is untracked. That needs to become an explicit include-list rather than a pattern blanket.
