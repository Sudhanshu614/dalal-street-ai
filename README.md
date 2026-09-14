# Dalal Street AI

**Ask questions about the Indian stock market in plain English — including about companies whose names and tickers have changed.**

Dalal Street AI is a natural-language interface over 30 years of NSE (National Stock Exchange of India) market data.

Ask it *"What is the price of Tata Motors?"* and it does not just look up a symbol. `TATAMOTORS` no longer trades — the company demerged, and the passenger-vehicle business now lists as `TMPV`. The resolver follows that chain and answers with the ticker that actually trades today.

Same for companies that renamed themselves. *"Orchid Chemicals & Pharmaceuticals Limited"* resolves to `ORCHIDPHAR`. A query naming a company that no longer exists returns a delisting notice rather than a wrong number, and an unrecognised name returns ranked suggestions instead of a hallucinated price.

That mapping problem — names and symbols drifting over 30 years while historical data stays filed under the old ones — is what most Indian-market tooling gets wrong, and it is what this project is actually about.

```
┌─────────────┐   HTTP    ┌──────────────┐  function calls  ┌──────────────┐
│  Streamlit  │ ────────► │   FastAPI    │ ───────────────► │ Gemini 2.5   │
│  frontend   │ ◄──────── │   backend    │ ◄─────────────── │    Flash     │
└─────────────┘           └──────┬───────┘                  └──────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   UniversalDataFetcher   │
                    ├──────────────────────────┤
                    │  TickerResolver (7-tier) │
                    │  GenericQueryBuilder     │
                    │  Reliability layer       │
                    └────────────┬─────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
        SQLite warehouse    jugaad-data          nselib
        (16 tables)         (live quotes)     (fallback)
```

---

## The interesting part: 7-tier ticker resolution

Most Indian-market projects break the moment a company renames itself or changes its symbol. Historical price data is filed under the *old* symbol; a user asking about the company uses the *new* name; and NSE publishes the mapping as a pile of CSVs that nobody joins properly.

`App/src/data_fetcher/ticker_resolver.py` resolves an arbitrary user string to a currently-tradeable NSE symbol by walking a confidence-scored cascade, returning at the first hit:

| Tier | Strategy | Confidence |
|-----:|----------|-----------:|
| 1 | Direct match against active `stocks_master` | 100 |
| 1b/1c | ETF direct / normalized match | 100 / 98 |
| 2 | Recursive symbol-change chase (A→B→C) via `symbol_change_events` | 100 |
| 2.5 | High-confidence fuzzy match on active tickers | ≥85 |
| 2.6 | Index alias resolution (`NIFTY50` → `NIFTY 50`) | — |
| 3 | Company name-change lookup via `name_change_events` | 100 / ≥75 fuzzy |
| 4 | Explicit delisting check | — |
| 5 | Demerger child correlation via `corporate_events` | 85 / 75 |
| 6 | Not found, with "did you mean" suggestions | — |

Tier ordering is deliberate and load-bearing. Stock fuzzy matching runs *before* index alias resolution specifically so that "Jio Financial Services" doesn't collapse into the "Nifty Financial Services" index. The recursive symbol chase means a ticker renamed twice still resolves.

> A good illustration: `BANKNIFTY` resolves to the **ticker** `BANKNIFTY1`, not the NIFTY BANK index — because `BANKNIFTY1` is a real active security and stock matching precedes index matching. That is the intended behaviour, and there is a regression test pinning it.

**Measured performance:** the resolver scores **73% overall accuracy** on the published [ticker-resolution benchmark](#datasets), including hard multi-hop cases like `TATATELECM → AVAYAGCL → AGCNET → BBOX`. Per-category numbers are in the benchmark card. Beating this is an open invitation.

If you only take one thing from this repo, take this module.

---

## What else is in here

| Component | File | What it does |
|---|---|---|
| **Universal data fetcher** | `App/src/data_fetcher/universal_data_fetcher.py` | One `fetch(query_type, params)` entry point instead of thousands of per-stock functions. Introspects the SQLite schema at startup via `PRAGMA`, then routes each query type through a primary → backup → fallback source chain with per-source timeouts. |
| **Generic query builder** | `App/src/data_fetcher/generic_query_builder.py` | Synthesises parameterised SQL from `(table, filters, fields, sort, limit)`. Every identifier is validated against the runtime-discovered schema; every value goes through a `?` placeholder. |
| **Reliability layer** | `App/src/reliability/` | Token-bucket rate limiter, exponential-backoff retry with jitter, and a three-state circuit breaker — configured per data source in `reliability_config.py`. |
| **LLM orchestration** | `App/api/server.py` | Multi-turn Gemini function-calling loop (max 5 turns), pre-tool ticker resolution with a confidence floor, LLM self-validation with auto-retry, and a hallucination monitor that flags numeric claims made without underlying data. |
| **Ingestion pipeline** | `App/scriptsrebuild/`, `App/scripts/rebuild/` | The scripts that build the warehouse from NSE bhavcopy, OpenChart, and public filings. See [`docs/REBUILD.md`](docs/REBUILD.md). |

---

## Data

**This repository does not ship a database.** The warehouse it was developed against is ~4 GB and derived from NSE and third-party sources whose terms do not permit redistribution. You build your own copy from primary sources using the included pipeline.

What that pipeline produces:

| Table | Approx. rows | Coverage |
|---|---:|---|
| `daily_ohlc` | 10.2 M | 5,261 symbols, 1995-02-08 → present |
| `market_etfs` | 7.2 M | |
| `market_indices` | 904 K | |
| `corporate_events` | 42 K | Splits, bonuses, dividends, demergers |
| `delisting_events` | 31 K | |
| `quarterly_results` | 25 K | |
| `annual_financials` | 21 K | |
| `stocks_master` | 2.6 K | |
| `fundamentals` | 2.6 K | |
| `name_change_events` | 2.3 K | |
| `symbol_change_events` | 1.0 K | |
| `ipo_data` | 1.3 K | |
| `fii_dii_data` | 1.5 K | |

Read [`docs/DATA.md`](docs/DATA.md) before you redistribute anything you build with this. It covers source provenance and the licensing constraints that apply to the data — which are **not** the same as this repository's code license.

### Datasets

Two artefacts are published separately from the code:

| Artefact | What it is |
|---|---|
| **NSE corporate actions & ticker lineage** | Listings, renames, symbol changes, corporate actions, delistings, IPO history. The joined lineage data that makes historical Indian market data usable. Optionally with price history. |
| **Ticker-resolution benchmark** | A labelled evaluation set — query string → expected symbol, across 8 categories and 3 difficulty levels, with a published baseline. As far as we know, the first for the Indian market. |

Build either yourself:

```bash
python scripts/export_dataset.py --tier core        # or --tier market
python scripts/build_resolution_benchmark.py
```

The exporter is tiered by redistribution risk and refuses to include the screener.in-scraped fundamentals without an explicit acknowledgement flag. [`docs/KAGGLE.md`](docs/KAGGLE.md) explains the reasoning and the publishing steps.

---

## Quickstart

**Prerequisites:** Python 3.11+, a [Gemini API key](https://aistudio.google.com/apikey), and build tooling for TA-Lib.

```bash
git clone https://github.com/Sudhanshu614/dalal-street-ai.git
cd dalal-street-ai

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r App/api/requirements.txt
pip install -r requirements.txt

cp .env.example .env               # then add your GEMINI_API_KEY
```

You now need a database. Either:

**A. Start with the bundled schema and a small sample** (fastest path to a running app):

```bash
python scripts/bootstrap_db.py --sample
```

**B. Build the full warehouse** from NSE primary sources — takes hours, see [`docs/REBUILD.md`](docs/REBUILD.md).

Then run both processes:

```bash
# terminal 1 — backend
python -m uvicorn App.api.server:app --host 127.0.0.1 --port 8000

# terminal 2 — frontend
streamlit run streamlit_app.py
```

Open http://localhost:8501.

### Docker

```bash
cp .env.example .env                      # add your GEMINI_API_KEY
python scripts/bootstrap_db.py --sample   # the repo ships no database
docker compose up --build
```

Frontend on http://localhost:8501, API on http://localhost:8000 (`/docs` for the OpenAPI page).

### Troubleshooting a fresh clone

| Symptom | Cause |
|---|---|
| Frontend loads but says *"Cannot reach the backend"* | The backend didn't start. Read its terminal output — it names the missing piece. |
| Backend exits with `GEMINI_API_KEY is not set` | Working as intended. Add a key to `.env` — [get one free](https://aistudio.google.com/apikey). |
| Backend exits with `Database not found` | Working as intended. Run `python scripts/bootstrap_db.py --sample`. |
| `pip install TA-Lib` fails | TA-Lib is a C library. Use Docker, or install the system package first (`brew install ta-lib`, or the [Windows wheel](https://github.com/cgohlke/talib-build/releases)). `pandas-ta` is the automatic fallback. |
| Everything installs, no data comes back | Check `python scripts/verify_db.py` — the sample database only covers ~2 years. |

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and edit.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | **Required.** Google Gemini API key. |
| `DB_PATH` | `App/database/stock_market_new.db` | Path to the SQLite warehouse. |
| `API_BASE_URL` | `http://localhost:8000` | Backend URL the frontend calls. |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | Backend bind address. |
| `CORS_ORIGINS` | `http://localhost:8501` | Comma-separated allowed origins. |
| `ADMIN_TOKEN` | — | Bearer token required by `/admin/*` endpoints. Unset disables them. |
| `PRICE_CACHE_TTL_SEC` | `60` | In-process live-price cache TTL. |
| `DEV_RELOAD` | `false` | Enable uvicorn autoreload and debug logging. |

---

## Project status

This is a working system, not a polished product. Known gaps are tracked honestly in [`docs/ROADMAP.md`](docs/ROADMAP.md) — including the bugs I know about and haven't fixed.

```bash
python -m pytest tests/ -q     # 165 tests, no API key or database required
```

Test coverage is real but uneven: the ticker resolver and query builder are well covered, the LLM orchestration layer is not. Runtime validation (LLM self-validation loop + hallucination monitor) covers some of that gap.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Ranked by how much it would help:

1. Tests for the LLM orchestration path in `App/api/server.py` — the least-covered part
2. Replacing the screener.in fundamentals scrape with a primary-filing or licensed source ([why](docs/DATA.md))
3. Consolidating the two overlapping ingestion directories
4. Postgres support alongside SQLite
5. **Beating 73% on the [ticker-resolution benchmark](#datasets)** — the most interesting open problem here

## License

Code is licensed under [Apache License 2.0](LICENSE) — Copyright 2026 Sudhanshu Bawane.

**Data is not.** Anything you ingest with this pipeline remains subject to the terms of its original source. See [`docs/DATA.md`](docs/DATA.md).

## Author

Built by **Sudhanshu Bawane** — [GitHub](https://github.com/Sudhanshu614) · sudhanshubawane.work@gmail.com

Issues and pull requests welcome. For security reports, see [`SECURITY.md`](SECURITY.md).

## Disclaimer

This project is for research and educational purposes. It is not investment advice, it is not affiliated with or endorsed by NSE India, and the data it produces may be incomplete or wrong. Do not make financial decisions based on its output.
