# What and why

<!-- What this changes, and why it should change. The what is in the diff;
     the why is not. One logical change per pull request. -->

## Linked issue

<!-- e.g. Closes #12, Part of #34. If there is no issue, say why this did not
     need discussing first. -->

## How it was tested

<!-- Commands you ran and what you observed. If you added tests, name them.
     If a change cannot be covered by a test, say what you did instead.

     pytest -v --tb=short          # what CI runs
     pytest tests/test_ticker_resolver.py -v
     pytest --run-integration      # only if you have the full database -->

## Resolver changes

<!-- Delete this section if you did not touch App/src/data_fetcher/ticker_resolver.py.

     Which tier changed, and why the tier ordering still holds. Tier order is a
     contract, not an implementation detail: tier 2.5 runs before tier 2.6 so
     that a company sharing words with an index (the "Jio Financial Services"
     versus "Nifty Financial Services" case) resolves to the stock. Each tier
     only runs when every tier above it declined. -->

## Checklist

- [ ] One logical change. Unrelated fixes are in a separate pull request.
- [ ] Tests added or updated for the behaviour that changed.
- [ ] `pytest` passes locally on a clean checkout — no API key, no network, no production database.
- [ ] No secrets committed. `git diff --cached` checked; no `.env`, no service-account JSON, no key pasted into a comment or a test.
- [ ] No database files or data dumps committed. `*.db`, `*.csv` and `*.xlsx` are gitignored for licensing reasons, not size — see `docs/DATA.md`.
- [ ] No absolute paths. Paths resolve from `Path(__file__)` or come from an environment variable.
- [ ] No hardcoded schema. The system prompt and the query builder both introspect the live database via `PRAGMA`; a typed-out table list drifts and has broken this project before.
- [ ] New configuration is an environment variable, documented in `.env.example` and the README table — not a constant in code or a committed config file.
- [ ] New dependencies are justified in the description above. The install is already heavy.
- [ ] Docs updated where behaviour changed — `README.md`, `docs/`, `tests/README.md`, or the docstrings.
- [ ] If `TickerResolver` was touched, the section above states which tier changed and why the ordering still holds.
