# Tests

Everything here runs offline. No network, no `GEMINI_API_KEY`, no production
database. A clean clone plus `pip install -r requirements-dev.txt` is enough.

## Running them

```bash
pip install -r requirements-dev.txt

pytest                       # the whole suite (~1.5s)
pytest -v --tb=short         # what CI runs
pytest tests/test_ticker_resolver.py -v
pytest -k "tier_2_5"         # one tier
```

Coverage for the modules under test:

```bash
pytest -q --cov=App --cov-report=
coverage report --show-missing \
  --include="App/config.py,App/src/data_fetcher/ticker_resolver.py,App/src/data_fetcher/generic_query_builder.py"
```

### Integration tests

Anything needing the real 3.9 GB database, the network or an API key is marked
`@pytest.mark.integration` and **skipped by default**:

```bash
pytest --run-integration
```

Keep it that way. A test that cannot run on a fresh CI runner belongs behind
this marker.

## Layout

| File | What it covers |
| --- | --- |
| `conftest.py` | Module loading, fixtures, the `integration` marker |
| `fixtures/build_fixture_db.py` | Generates the synthetic database |
| `test_ticker_resolver.py` | One test per resolution tier, plus ordering and idempotency |
| `test_generic_query_builder.py` | SQL construction and the injection boundary |
| `test_config.py` | Environment precedence, path resolution, `require_gemini_key()` |

## How the fixture database works

`tests/fixtures/build_fixture_db.py` builds a ~136 KB SQLite file containing
about 90 rows of **entirely invented** data. `conftest.py` builds it once per
session into pytest's temporary directory via the `fixture_db_path` fixture.

Two decisions worth knowing:

**It is generated, not committed.** `.gitignore` excludes `*.db` across the
whole repository, so a checked-in fixture would be silently dropped and CI
would fail on a fresh clone. Generating it takes milliseconds and guarantees
the data can never drift from the generator that documents it. To inspect it
by hand:

```bash
python tests/fixtures/build_fixture_db.py --out /tmp/fixture.db
sqlite3 /tmp/fixture.db "SELECT symbol, company_name FROM stocks_master"
```

**It is synthetic, not sampled.** The production data is licence-encumbered
NSE data and must never enter the repository. The symbols and company names
are shaped like Indian-market data so the resolver's normalisation rules get a
realistic workout, but no real company appears. The DDL *is* copied verbatim
from the production database's `sqlite_master`, so the fixture enforces the
same CHECK, UNIQUE and FOREIGN KEY constraints the real data satisfies.

### Which row exercises which tier

| Tier | Method string | Fixture data | Example query |
| --- | --- | --- | --- |
| 1 | `direct` | `stocks_master` active row | `AGNIMOTORS` |
| 1b | `etf_direct` | `market_etfs.index_name = 'NIFTYBEES-EQ'` | `NIFTYBEES` |
| 1c | `etf_normalized` | same row, normalised | `NIFTY BEES`, `GOLDBEE` |
| 2 | `symbol_change` | `symbol_change_events` two-hop chain | `PURVAENG` -> `PURVAINFRA` -> `SETUINFRA` |
| 2.5 | `fuzzy_match_high_conf` | active `stocks_master` row | `TARAPHARM` -> `TARAPHARMA` |
| 2.6 | `index_alias` | `market_indices.index_name` | `BANKNIFTY` -> `NIFTY BANK` |
| 3 | `name_change` | `name_change_events` (LIKE) | `Kaveri Mills Limited` -> `KAVERITEX` |
| 3 | `name_change_fuzzy` | `name_change_events` (>=75 fuzzy) | `Rudra Chemcials Ltd` -> `RUDRACHEM` |
| 4 | `delisted` | `delisting_events` | `ORIONMET` |
| 5 | `demerger_single_child` | `corporate_events` + one matching `listing_date` | `HIMGIRICON` -> `HIMGIRIRE` |
| 5 | `demerger_multiple_children` | `corporate_events` + two matching `listing_date`s | `MERUGROUP` |
| 6 | `not_found` | nothing matches | `ZZZQQQ` |

## Two things to read before changing anything

**Modules are loaded by file path, not imported as a package.**
`App/src/data_fetcher/__init__.py` imports `universal_data_fetcher`, which
pulls in jugaad-data, nselib, pandas-ta and TA-Lib. TA-Lib needs a C toolchain,
so a plain `from App.src.data_fetcher.ticker_resolver import ...` would make CI
slow and brittle for nothing. `conftest.py` uses
`importlib.util.spec_from_file_location` so only the file under test executes.
If you add a test for another module in that package, load it the same way.

**Tier order is part of the contract.** The resolver is a waterfall; each tier
only runs when every tier above it declined. Every test therefore asserts
`resolution_method` as well as the symbol, so a test cannot pass with the right
answer arrived at from the wrong tier.

`test_tier_2_5_runs_before_2_6_so_stocks_beat_indices` is the one to be most
careful with. Index resolution used to sit above the fuzzy stock match, which
made a company sharing words with an index collapse into the index (upstream:
"Jio Financial Services" being swallowed by the "Nifty Financial Services"
index). Do not reorder those two tiers.

## Adding a resolver test case

This is the most common contribution. Four steps:

**1. Add the fixture rows.** Edit the relevant list in
`tests/fixtures/build_fixture_db.py` — `STOCKS_MASTER`, `SYMBOL_CHANGE_EVENTS`,
`NAME_CHANGE_EVENTS`, `DELISTING_EVENTS`, `CORPORATE_EVENTS`, `INDEX_NAMES` or
`ETF_NAMES`. Invent the data; do not copy it from the production database.

**2. Check your symbol does not get caught by an earlier tier.** This is where
new cases usually go wrong. A symbol meant to test Tier 4 will never reach it
if Tier 2.5 fuzzy-matches it to an active ticker first. Two things in
particular:

- Keep new symbols under ~0.85 `difflib` similarity to every active symbol,
  and their company names under ~0.85 token similarity to every active
  company name.
- `listing_date` must stay more than 30 days away from every `ex_date` in
  `CORPORATE_EVENTS`, or your stock will be picked up as a demerger child and
  break the Tier 5 tests.

**3. Confirm the real behaviour before writing the assertion.** Print it, do
not guess:

```bash
python - <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location(
    "tr", "App/src/data_fetcher/ticker_resolver.py")
tr = importlib.util.module_from_spec(spec); sys.modules["tr"] = tr
spec.loader.exec_module(tr)

import subprocess; subprocess.run(
    [sys.executable, "tests/fixtures/build_fixture_db.py", "--out", "/tmp/fx.db"])

print(tr.TickerResolver("/tmp/fx.db").resolve("YOUR_SYMBOL"))
PY
```

**4. Write the test.** Assert the symbol, the confidence band and the
`resolution_method`. Add a docstring saying what would break if the tier order
changed.

Two shapes to remember:

- Index results use `resolved_index_name`, **not** `resolved_ticker` — there is
  no `resolved_ticker` key at all on an index result.
- `delisted`, `demerger_multiple_children` and `not_found` return
  `resolved_ticker = None` while still carrying a confidence. `delisted` is
  confidence **100**: a confident negative. Branch on `resolved_ticker`, never
  on confidence alone.

If a test fails because the resolver does something other than what you
expected, fix the test, not the resolver — unless you are certain it is a bug,
in which case open an issue first.
