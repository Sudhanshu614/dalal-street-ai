# Data: sources, provenance, and what you may do with it

**Short version: the Apache-2.0 licence on this repository covers the code. It does not cover, and cannot cover, the market data the code ingests. Read this before you publish, share, or sell anything you build with this pipeline.**

This is not legal advice. If you intend to use this commercially, get an opinion from someone qualified.

---

## Why no database is included

The warehouse this project was developed against is roughly 4 GB and is assembled from four upstream sources, none of which grant redistribution rights. Publishing it — as a repo asset, as a Kaggle dataset, or as a download link — would be republishing other people's data.

So the repository ships a **schema and a rebuild pipeline** instead. You point it at the primary sources and it builds your own copy. That copy is yours to use under the terms below.

---

## Where each table comes from

| Table(s) | Upstream source | How it is obtained |
|---|---|---|
| `daily_ohlc`, `bhavcopy_history`, `download_log` | **NSE India** daily bhavcopy | `App/src/data_fetcher/bhavcopy_downloader.py` — NSE archives CSV, with `jugaad-data` and `nselib` as fallbacks |
| `daily_ohlc` (historical backfill) | **OpenChart** | `App/scriptsrebuild/02_master_rebuild.py` — 20+ year OHLCV backfill |
| `market_indices`, `market_etfs` | **NSE India** / OpenChart | `App/scripts/rebuild/10_daily_update_indices_etfs.py`, `11_scrape_all_indices_openchart.py` |
| `stocks_master`, `name_change_events`, `symbol_change_events`, `corporate_events`, `delisting_events` | **NSE India** published CSV/XLSX filings | Manual download + `App/scriptsrebuild/04_daily_nse_update.py` |
| `fundamentals`, `quarterly_results`, `annual_financials` | **screener.in** (HTML scrape) | `App/scriptsrebuild/02_master_rebuild.py`, `06_rescrape_failed_stocks.py`, `08_scrape_enhanced_fundamentals.py` |
| `ipo_data` | **Chittorgarh** | `App/scriptsrebuild/10_scrape_ipo_data_chittorgarh.py` |
| `fii_dii_data` | **NSE India** via `nselib` | `App/scriptsrebuild/14_scrape_fii_dii_data.py` |

---

## Constraints you inherit

### NSE India

NSE asserts copyright over the content on its website and states that no portion may be reproduced on, transmitted to, or stored in another website or electronic retrieval system. It permits viewing, printing, and downloading **for personal, non-commercial or educational purposes**, unmodified and with acknowledgement of source. Redistribution and commercial use require a separate agreement with NSE Data.

- [NSE copyright notice](https://www.nseindia.com/static/nse-copyright)
- [NSE Data Sharing & Usage Policy](https://www.nseindia.com/static/market-data/nse-data-policy)

**Practical reading:** building a local database for your own research or education is the use NSE describes. Publishing that database, or selling access to it, is not.

### screener.in

The fundamentals tables are **scraped HTML from a third-party aggregator**, not primary filings. screener.in did their own collection and normalisation work; that output is theirs. Republishing a scrape of an aggregator is the highest-risk category of scraping activity — operational scraping for your own use is broadly tolerated in practice, resale and redistribution is what draws litigation.

**If you plan to distribute anything:** replace this source. Company financials are available from primary filings (NSE/BSE corporate filings, MCA) and from licensed vendors. Swapping the fundamentals scraper for a licensed feed is a tracked contribution — see [`ROADMAP.md`](ROADMAP.md).

### Chittorgarh

Same category as screener.in — a third-party aggregator. Same caution applies to `ipo_data`.

### `jugaad-data` and `nselib`

These are MIT/Apache-licensed Python packages. The **libraries** are freely usable. The **data they return** is still NSE's, and the terms above apply to it unchanged. A permissive wrapper around a restricted source does not launder the source.

---

## What this means in practice

| You want to… | Position |
|---|---|
| Run this locally for your own research or learning | Fine — this is the use NSE's policy describes |
| Fork the code, modify it, build a product on it | Fine under Apache-2.0, but you need your own data licence for the data |
| Publish your built database on Kaggle / HuggingFace / a torrent | **Don't.** This is redistribution of NSE + screener.in data |
| Sell API access to data this produces | **Don't**, without an NSE Data agreement |
| Publish *derived aggregate statistics* (e.g. "count of NSE name changes per year") | Lower risk — factual aggregates are further from the original expression, but get advice if it matters |
| Publish the **ticker-resolution mapping tables** | Grey. `name_change_events` / `symbol_change_events` are close to raw NSE filings. Ask first |

---

## If you want a shareable dataset anyway

The defensible version is not a copy of the warehouse. It is a **derived artefact** that is your own work:

- Aggregate statistics over corporate actions (name changes, symbol changes, demergers per year/sector)
- A benchmark/eval set of ticker-resolution test cases — query string → expected symbol — which is *your* labelling effort, not NSE's data
- Synthetic or heavily transformed data for testing the pipeline

A ticker-resolution benchmark is the genuinely novel contribution here and nobody else has published one. That is a far better Kaggle upload than 10 million price rows anyone can re-download.

---

## Reporting a problem

If you are a rights holder and believe this project's pipeline or documentation oversteps, open an issue and it will be addressed promptly.
