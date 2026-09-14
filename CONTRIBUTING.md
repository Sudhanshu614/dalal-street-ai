# Contributing

Thanks for looking. This project is a working system built by one person, and it shows in places — see [`docs/ROADMAP.md`](docs/ROADMAP.md) for an honest list of what is broken and missing.

## Setup

```bash
git clone https://github.com/Sudhanshu614/dalal-street-ai.git
cd dalal-street-ai
python -m venv .venv && source .venv/bin/activate
pip install -r App/api/requirements.txt -r requirements.txt -r requirements-dev.txt
cp .env.example .env          # add your GEMINI_API_KEY
python scripts/bootstrap_db.py --sample
python -m pytest tests/ -q    # should be 164 passed, 1 skipped
```

You do not need the full 4 GB warehouse to contribute. The sample database exercises every code path except long-range historical queries, and the test suite needs neither a database nor an API key.

## Where the value is

Ranked by how much it would help:

1. **Tests for the LLM orchestration path** in `App/api/server.py` — the function-calling loop, self-validation retry, and hallucination monitor. It is the least-covered part of the system because it needs a live Gemini key; mocked responses would fix that.
2. **Replacing the screener.in scrape** with a primary-filing or licensed source. This is the main thing blocking anyone from distributing a built database. See [`docs/DATA.md`](docs/DATA.md).
3. **Beating 73%** on the [ticker-resolution benchmark](docs/KAGGLE.md). Build it with `python scripts/build_resolution_benchmark.py`. The hard categories are `multi_hop_symbol` and `demerger_child`.
4. **Consolidating `App/scriptsrebuild/` and `App/scripts/rebuild/`** into one pipeline.
5. **Postgres support** alongside SQLite.
6. Bug fixes from the [known issues](docs/ROADMAP.md#known-bugs) table.

## Ground rules

**Never commit secrets.** `.env`, `*service-account*.json`, and `.private/` are gitignored. Check `git diff --cached` before every commit. If you leak a key, revoke it first and tell us second.

**Never commit database files or data dumps.** `*.db`, `*.csv`, `*.xlsx` are gitignored for licensing reasons, not just size. See [`docs/DATA.md`](docs/DATA.md).

**No absolute paths.** Resolve from `Path(__file__)` or read an environment variable. This repository previously had 25 hardcoded `E:\...` paths; do not add a 26th.

**No hardcoded schema.** The system prompt and query builder both introspect the live database via `PRAGMA`. If you find yourself typing a table name into a prompt string, stop — that drift is exactly what broke this before.

**Configuration goes in environment variables**, documented in `.env.example` and the README table. Not in code, not in a committed config file.

## Pull requests

- One logical change per PR
- Explain *why*, not just *what* — the what is in the diff
- If you touch `TickerResolver`, say which tier you changed and why the ordering still holds
- If you add a dependency, justify it — the install is already heavy (TA-Lib, scipy, two NSE libraries)

## Reporting issues

Include your Python version, OS, whether you are on the sample or full database, and the full traceback. For resolver bugs, include the exact query string and what you expected it to resolve to — those make excellent test cases.

## Security

Do not open a public issue for a security problem. See [`SECURITY.md`](SECURITY.md).
