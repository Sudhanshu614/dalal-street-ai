# Publishing the dataset

How to publish this project's data on Kaggle (or anywhere else), and — more importantly — what to leave out and why.

Read [`DATA.md`](DATA.md) first. This document assumes you have.

---

## The short version

Publish **two** artefacts, not one:

1. **`dalal-street-nse-corporate-actions`** — the `core` or `market` tier export
2. **`ticker-resolution-benchmark-nse`** — the labelled evaluation set

Do **not** publish `fundamentals`, `quarterly_results`, or `annual_financials`. The exporter will not let you without an explicit flag, and the reasoning is below.

```bash
python scripts/export_dataset.py --tier market --format both --output dist/dataset
python scripts/build_resolution_benchmark.py --output dist/benchmark
```

---

## Why the fundamentals tables are excluded

This is the one place where "just publish everything" is the wrong call, so it is worth being precise about the reasoning rather than asking you to take it on faith.

| | `daily_ohlc` etc. | `fundamentals` etc. |
|---|---|---|
| **Origin** | NSE India — the exchange itself | screener.in — a private company |
| **Nature** | Facts published by a regulator | Another firm's collected and normalised product |
| **Their business** | NSE sells data as a side business | screener.in's *entire* business is this data |
| **Precedent** | Dozens of NSE price datasets already on Kaggle | Effectively none |
| **Volume here** | ~18 million rows | ~48,000 rows |

The asymmetry is the point. The fundamentals are **0.3% of the dataset by volume** and carry the large majority of the takedown risk. You are not withholding anything users want — you are declining to republish a competitor's product.

There is also a separate consideration: uploading data you do not have distribution rights to violates **Kaggle's own Terms of Use**, independent of anything NSE or screener.in might do. That is an account-level risk, not just a dataset-level one.

If you want fundamentals in the published dataset, the right fix is to re-source them from primary filings (NSE/BSE corporate filings, MCA) rather than from an aggregator. That is a tracked item in [`ROADMAP.md`](ROADMAP.md) and a genuinely valuable contribution.

---

## Choosing a tier

| Tier | Adds | Rows | Risk | Use when |
|---|---|---:|---|---|
| `core` | Corporate filings, ticker lineage | ~80 K | **Low** — public-record regulatory disclosures | You want the maximally defensible version. This is the differentiated part |
| `market` | + OHLC, indices, ETFs, FII/DII | ~18 M | **Medium** — NSE restricts redistribution, but many similar datasets exist | You want the dataset to be immediately useful for backtesting and analysis |
| `full` | + scraped fundamentals | ~18 M | **High** | Not recommended. Requires `--i-understand-fundamentals-are-scraped` |

**Recommendation: publish `market`.** It gives users a genuinely complete picture, matches what already exists on Kaggle, and stops short of the one thing that would be hard to defend.

If you would rather be conservative, `core` alone is still a novel and useful dataset — nobody has published joined NSE ticker lineage before.

---

## Publishing to Kaggle

### 1. Build the export

```bash
python scripts/export_dataset.py --tier market --format both --compress
```

Produces in `dist/dataset/`:

```
data/                    one CSV (and Parquet) per table
schema_only.db           empty SQLite with the full schema
README.md                the dataset card, generated from the actual export
LICENSE.txt              the data licence notice
manifest.json            row counts, columns, date ranges, per-file SHA-256
CHECKSUMS.txt            sha256sum -c compatible
```

### 2. Verify before uploading

```bash
cd dist/dataset && sha256sum -c CHECKSUMS.txt
python -c "import json; m=json.load(open('manifest.json')); print(sum(t['rows'] for t in m['tables'].values()), 'rows')"
```

Read the generated `README.md` end to end. It is what people will judge the dataset by, and it is generated from real numbers — if something looks wrong, it *is* wrong.

### 3. Create the dataset

Kaggle's public dataset limit is 200 GB, so size is not a constraint here.

- **Title:** `NSE India — Corporate Actions, Ticker Lineage & Price History`
- **Subtitle:** lead with the differentiator, not the size. Something like *"30 years of NSE data with the company-rename and symbol-change mappings joined"*
- **Licence:** select **Other** and paste `LICENSE.txt`. Do **not** select CC0 or a permissive Creative Commons licence — you do not hold those rights to grant
- **Description:** paste the generated `README.md`
- **Tags:** `finance`, `stocks`, `india`, `time series`, `nse`, `investing`
- **Provenance:** fill in the sources field honestly — NSE India, OpenChart, Chittorgarh

Via CLI:

```bash
pip install kaggle
cd dist/dataset
kaggle datasets init -p .
# edit dataset-metadata.json — set title, id, and licenses to "other"
kaggle datasets create -p . --dir-mode zip
```

### 4. Publish the benchmark separately

```bash
python scripts/build_resolution_benchmark.py --per-category 500
```

- **Title:** `Ticker Resolution Benchmark — NSE India`
- **Licence:** CC BY 4.0 for the benchmark construction (see the note in its generated README — the labelling is your work; the underlying facts are NSE's)
- **Description:** paste `dist/benchmark/README.md`

Keep this one separate rather than folding it into the main dataset. It is a different kind of artefact — an evaluation set, not raw data — and it is the piece most likely to get cited.

---

## Linking the two together

The dataset and the repository should point at each other in both directions:

- Dataset card → GitHub repo, `docs/REBUILD.md` (so people can rebuild rather than trust), and `docs/DATA.md`
- README → both Kaggle datasets (add the URLs to the `### Datasets` section once they exist)
- Both dataset cards → each other

This is the difference between "someone dumped a CSV" and a project. The reproducibility link matters most: a dataset that shows you exactly how it was built is worth far more than one that asks you to take it on faith.

---

## Keeping it current

The data goes stale immediately — NSE trades every weekday.

```bash
# after your daily pipeline run (see REBUILD.md)
python scripts/export_dataset.py --tier market --force
kaggle datasets version -p dist/dataset -m "Data through YYYY-MM-DD"
```

Monthly is a reasonable cadence. State the update frequency on the dataset card and then actually keep to it — an abandoned dataset that claims to be maintained is worse than one honestly marked as a snapshot.

---

## If you get a takedown notice

Unlikely, but have a plan:

1. **Comply first, argue later.** Unpublish immediately. A dataset is not worth a dispute
2. **Do not delete the repository.** The code is Apache-2.0 and yours — a data complaint does not touch it
3. **Fall back to `core`,** or to schema-plus-rebuild-scripts only. The project still works; users just build their own copy
4. **Open an issue** documenting what happened, so the next person understands the boundary

The rebuild pipeline is exactly what makes this survivable. Users can always regenerate the data from primary sources, which means the project's value never actually depended on your hosting the bytes.

---

## What not to do

- Don't select a CC0 or CC BY licence on the market data. You cannot grant rights you don't hold
- Don't claim the data is "official" or "NSE-endorsed". It is neither
- Don't publish `download_log`, `bhavcopy_history`, or `metadata`. They describe your machine's ingestion runs and mean nothing to anyone else. The exporter excludes them
- Don't upload the raw 3.9 GB `.db` file. Tables as CSV/Parquet plus a schema-only database is more useful, more inspectable, and easier to version
- Don't skip the dataset card. An undocumented dataset gets ignored no matter how good the data is
