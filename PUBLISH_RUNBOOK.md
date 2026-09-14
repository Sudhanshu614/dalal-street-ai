# Publish runbook

> **Just want it done?** Two commands:
>
> ```powershell
> cd "E:\Dalal Street Trae"
> .\publish.ps1 -DryRun     # preview: changes nothing
> .\publish.ps1             # real run: asks before anything irreversible
> ```
>
> `publish.ps1` automates every step below. It runs `preflight.ps1` first and refuses to
> continue if anything is unsafe. It stops and asks before commit, push and Kaggle upload.
>
> If PowerShell blocks the script:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
>
> The rest of this file is the manual version — read it if you want to know what the script
> is doing, or if you prefer to run things yourself.

Delete this file (and both `.ps1` scripts) before your final commit — they are scaffolding,
not project documentation.

---

## Step 0 — Revoke the leaked credentials (do this first, today)

Five credentials sat in plaintext on this machine for months. They are quarantined in
`.private/` and were never committed to git, but assume they are compromised.

Open `.private\Config.md` and `.private\env.backup` to see the exact values, then:

| Credential | Where to revoke |
|---|---|
| 2× GitHub PAT (`ghp_…`) | https://github.com/settings/tokens → Delete |
| Gemini API key (`AIza…`) | https://aistudio.google.com/apikey → Delete |
| Groq API key (`gsk_…`) | https://console.groq.com/keys → Delete |
| GCS service-account key | GCP Console → IAM & Admin → Service Accounts → `dalal-street-uploader` → Keys → Delete |

Then create a fresh Gemini key and wire it up:

```powershell
cd "E:\Dalal Street Trae"
Copy-Item .env.example .env
notepad .env          # paste the NEW GEMINI_API_KEY, save
```

Nothing else in this runbook depends on step 0, but do not skip it.

---

## Step 1 — Verify the repo is sane

```powershell
cd "E:\Dalal Street Trae"

# Python deps for the tooling
python -m pip install -r requirements-dev.txt

# 165 tests, no API key or database needed
python -m pytest tests/ -q
```

Expect: `164 passed, 1 skipped`.

```powershell
# Confirm nothing sensitive is about to be committed
git status --short
git check-ignore -v .env .private/Config.md App/database/stock_market_new.db
```

The `check-ignore` command should print a rule for each path. If any path prints nothing,
stop — it is not ignored and would be committed.

---

## Step 2 — Normalise line endings

The repo was written on Windows with CRLF throughout, which makes every diff look like a
full-file rewrite. `.gitattributes` now sets `* text=auto eol=lf`, but that only takes
effect after a one-time renormalise:

```powershell
git add --renormalize .
git status --short
```

---

## Step 3 — Verify the app actually runs

This is the one thing that was never tested end to end. Do it before you announce anything.

```powershell
# Build a small database (takes ~30s from your existing full DB)
python scripts/bootstrap_db.py --sample

# Sanity-check it
python scripts/verify_db.py
```

Expect exit code 0 and 7/7 resolver fixtures passing.

Then boot the stack:

```powershell
docker compose up --build
```

Open http://localhost:8501 and ask it something like *"What is the price of TCS?"*.

**Known risk:** `App/frontend/Dockerfile` installs `TA-Lib>=0.4.29` and relies on a prebuilt
wheel existing. If the build fails there, that is the bug to fix — it will hit every
first-time cloner.

If Docker is not installed, run the two processes directly instead:

```powershell
# terminal 1
python -m uvicorn App.api.server:app --host 127.0.0.1 --port 8000

# terminal 2
streamlit run streamlit_app.py
```

---

## Step 4 — Push to GitHub

Your repo is already public at **https://github.com/Sudhanshu614/dalal-street-ai-** (note the
trailing hyphen — 40 commits). Every file in this repo now points at `dalal-street-ai`
*without* the hyphen, so **rename the repo before you push**:

> GitHub → **Settings → General → Repository name** → `dalal-street-ai` → **Rename**

GitHub redirects the old URL, and your existing `origin` remote keeps working. If you'd
rather not rename, find-and-replace `dalal-street-ai` → `dalal-street-ai-` across
`README.md`, `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/config.yml`, `scripts/export_dataset.py`
and this file first.

Optionally point your local remote at the new name:

```powershell
git remote set-url origin https://github.com/Sudhanshu614/dalal-street-ai.git
```

Then it's a normal commit on top of `main`. No force-push, no history rewrite.

```powershell
git add -A
git commit -m "Open-source release: docs, tests, CI, data tooling

- Add README, LICENSE (Apache-2.0), CONTRIBUTING, SECURITY, CODE_OF_CONDUCT
- Add 165 tests for ticker resolver, query builder and config
- Add GitHub Actions CI with gitleaks secret scanning
- Track the full ingestion pipeline (previously gitignored)
- Add bootstrap_db, verify_db, export_dataset, build_resolution_benchmark
- Replace all hardcoded paths with env-driven configuration
- Harden API: admin auth, configurable CORS, no debug-reload default
- Generate the LLM system prompt from live schema introspection
- Remove personal content and deployment transcripts"

git push origin main
```

Then in the GitHub web UI.

**Any time — before or after the push:**

1. **Settings → General → Features →** tick **Discussions** (the issue-template links 404 without it)
2. **Issues → Labels → New label →** create `data`, `ticker-resolver`, `dependencies`, `ci`
   (these are referenced by the issue forms and silently dropped if missing)
3. **Settings → General →** set the description and topics:
   `python` `nse` `indian-stock-market` `llm` `gemini` `streamlit` `fastapi` `sqlite` `finance`

**Only after the push:**

4. **Actions** tab → a workflow run named **CI** appears within ~30 seconds and should go green.

   If the Actions tab instead shows *"Get started with GitHub Actions"* with a grid of suggested
   workflow templates, that means **zero workflows exist on GitHub yet** — `.github/workflows/ci.yml`
   is still only on your machine. Push first. Do not click "Configure" on any of those templates;
   you already have a CI workflow and adding a second one will just create noise.
5. **Settings → General → Social preview →** upload an image if you have one. Optional, but
   it is what shows when the repo gets shared anywhere.

---

## Step 5 — Build the Kaggle datasets

Two separate uploads. Build them from your **full** database, not the sample.

```powershell
# Dataset 1 — the market data
python scripts/export_dataset.py --tier full `
  --i-understand-fundamentals-are-scraped `
  --format both --compress `
  --output dist/dataset

# Dataset 2 — the ticker-resolution benchmark
python scripts/build_resolution_benchmark.py --per-category 500 --output dist/benchmark
```

The full-tier export streams ~18 million rows. Expect it to take a while; it prints progress.

Verify before uploading:

```powershell
cd dist/dataset
Get-Content manifest.json | ConvertFrom-Json | Select-Object -ExpandProperty tables
```

Read the generated `dist/dataset/README.md` end to end — it is what people will judge the
dataset by, and every number in it comes from the actual export.

### Upload

```powershell
python -m pip install kaggle
# put your kaggle.json in %USERPROFILE%\.kaggle\

cd dist/dataset
kaggle datasets init -p .
notepad dataset-metadata.json
```

In `dataset-metadata.json` set:

- `title`: `NSE India — Corporate Actions, Ticker Lineage & Price History`
- `id`: `<your-kaggle-username>/nse-india-corporate-actions-ticker-lineage`
- `licenses`: `[{"name": "other"}]` — **not** CC0 or CC-BY. You do not hold those rights to grant

```powershell
kaggle datasets create -p . --dir-mode zip
```

Repeat for `dist/benchmark` with:

- `title`: `Ticker Resolution Benchmark — NSE India`
- `licenses`: `[{"name": "CC-BY-4.0"}]` — the labelling is your own work

Full reasoning and the tier trade-offs are in [`docs/KAGGLE.md`](docs/KAGGLE.md).

---

## Step 6 — Link them together

Once both Kaggle datasets exist, add their URLs to the `### Datasets` section of `README.md`,
and point each dataset card back at the GitHub repo. A dataset that shows exactly how it was
built is worth far more than one that asks to be taken on faith — that link is the whole
argument for this project.

```powershell
git add README.md
git commit -m "Link published Kaggle datasets"
git push
```

---

## Step 7 — Clean up

```powershell
Remove-Item PUBLISH_RUNBOOK.md
git add -A
git commit -m "Remove publish runbook"
git push
```

Keep `.private/` on your machine. It is gitignored and holds your originals.
