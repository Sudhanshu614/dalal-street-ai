# Roadmap and known issues

Honest state of the project. Nothing here is hidden from you in the code — it is written down so you do not have to discover it.

## Known bugs

| Issue | Location | Impact |
|---|---|---|
| Two overlapping ingestion directories | `App/scriptsrebuild/` vs `App/scripts/rebuild/` | Duplicate scripts with divergent behaviour (`04_daily_nse_update.py` vs `10_daily_update_nse.py`). Confusing for newcomers. |
| Bookkeeping write is not atomic with the data write | `bhavcopy_downloader.py` `update_daily()` | OHLC rows are committed first, then the `bhavcopy_history` log row. Anything raising in between — historically a `print()` of a non-ASCII character on a Windows cp1252 console — commits the prices and silently skips the log row. Harmless (that table is operational and never exported) but it makes the audit trail lie. Fix: write both in one transaction, or wrap the load so a display error cannot skip it. |
| `CorporateActionsIngester` import missing | `App/api/server.py` | `/admin/update/corporate_actions` returns 500. Guarded by try/except so the server still starts. |
| Dead `stock_aliases` SQL | `App/src/data_fetcher/bhavcopy_downloader.py` | Queries a table that was dropped. Dead code path. |
| Case-sensitive path bug | `Scripts/query_index_price.py` | Uses `App/Database/` (capital D) — fails on Linux/macOS. |
| Duplicate `_load_index_names` | `App/src/data_fetcher/ticker_resolver.py` | Method defined twice; the second silently shadows the first. Also called twice at init. |
| `market_indices` casing inconsistency | `App/src/data_fetcher/generic_query_builder.py` | Stored data mixes `'NIFTY 50'` and `'Nifty 50'`; worked around with `UPPER()` instead of normalising at ingest. |
| No-op spinner | `App/frontend/streamlit_app.py` | `with st.spinner(...): pass` — does nothing. |

## Known data gaps

The reference warehouse has three holes in `daily_ohlc` that `scripts/verify_db.py` reports:

| Range | Weekdays missing | Likely cause |
|---|---:|---|
| 2014-10-16 → 2014-11-07 | 15 | Partly Diwali/Muhurat clustering, partly a real gap |
| 2021-03-25 → 2021-05-06 | 29 | Real gap |
| 2021-05-19 → 2021-09-03 | **76** | Real gap — roughly 3.5 months |

NSE holidays are not modelled by the gap detector, so short runs can be false positives. The
2021 ones are not: 76 consecutive missing weekdays is a backfill that never ran.

Fix with:

```bash
python App/DAILY_RUNNER.py --start 2021-05-19 --end 2021-09-03 --force
python App/DAILY_RUNNER.py --start 2021-03-25 --end 2021-05-06 --force
python scripts/verify_db.py          # confirm the gaps are closed
```

If you publish a dataset built from a warehouse with these gaps, say so on the dataset card.

## Missing

- **Uneven test coverage.** 165 tests cover `ticker_resolver.py` (83%), `generic_query_builder.py` (100%) and `config.py` (95%). The LLM orchestration in `App/api/server.py` has **no tests at all** — it needs a live API key, so it was left to runtime validation (the self-validation loop and hallucination monitor). **This is now the highest-value contribution.**
- **No Postgres support.** SQLite only. Fine for single-user, a hard ceiling for anything shared.
- **No streaming responses.** The frontend blocks for up to 150 s on a single POST.
- **No conversation persistence.** History lives in Streamlit session state and dies on refresh.
- **Docker build is unverified end to end.** `App/frontend/Dockerfile` installs `TA-Lib>=0.4.29` and relies on a prebuilt wheel existing rather than building the C library. If `docker compose up --build` fails for you, this is why.

## Planned

### Near term
- Tests for the LLM orchestration path (mocked Gemini responses)
- Consolidate the two ingestion directories into one
- Replace the screener.in fundamentals scrape with a licensed or primary-filing source ([why](DATA.md))
- Beat the 73% baseline on the ticker-resolution benchmark

### Later
- Postgres backend alongside SQLite
- Streaming token responses to the frontend
- Real-time price WebSocket instead of the 60 s poll cache
- Options analytics beyond the raw chain
- Multi-provider LLM support that actually works — the `LLM_PROVIDER` abstraction exists but is not wired to the request path

## Design decisions worth knowing

**Tier ordering in the resolver is load-bearing.** Stock fuzzy matching (tier 2.5) runs before index alias resolution (tier 2.6) specifically so "Jio Financial Services" resolves to the stock and not the "Nifty Financial Services" index. If you reorder these, that test fails.

**The system prompt is generated, not hand-written.** An earlier version hardcoded the schema in the prompt and drifted badly out of sync with the actual database — the model was being told about five tables that no longer existed and was actively instructed to query one of them. It is now introspected from `PRAGMA` at startup. Do not reintroduce a hand-typed schema block.

**Routing is hand-tuned, not discovered.** `_load_routing_matrix()` in `universal_data_fetcher.py` is a hand-written table of primary/backup/fallback sources per query type, with measured latencies in the comments (jugaad-data is ~10x faster than nselib for live quotes, ~14x for option chains). This is deliberate — it encodes benchmark results that cannot be inferred at runtime.
