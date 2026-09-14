<#
.SYNOPSIS
    One-command publish pipeline for Dalal Street AI.

.DESCRIPTION
    Runs every step of the release in order. Stops and asks before each
    irreversible action (commit, push, Kaggle upload). Nothing is published
    without you typing 'yes'.

    Safe steps run automatically:
      preflight -> line-ending normalisation -> sample DB -> DB verify -> docker build

    Gated steps ask first:
      git commit -> git push -> dataset export -> benchmark -> Kaggle upload

.PARAMETER DryRun
    Run everything safe, skip every gated step. Nothing is committed or pushed.

.PARAMETER SkipDocker
    Skip the container build test (slow, needs Docker Desktop running).

.PARAMETER SkipDataset
    Skip building the Kaggle exports.

.PARAMETER Yes
    Auto-approve the commit gate only. Push and Kaggle upload still ask.

.EXAMPLE
    .\publish.ps1 -DryRun      # see what would happen, change nothing
    .\publish.ps1              # full run with prompts
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$SkipDocker,
    [switch]$SkipDataset,
    [switch]$Yes
)

# NOT 'Stop'. git writes ordinary progress and warnings to stderr, and with
# ErrorActionPreference='Stop' PowerShell turns any stderr line from a native
# executable into a terminating NativeCommandError. That aborts the script on
# messages like "CRLF will be replaced by LF", which are success, not failure.
# Every git call below checks $LASTEXITCODE explicitly instead.
$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'
Set-Location -Path $PSScriptRoot

$RepoOwner = 'Sudhanshu614'
$RepoName  = 'dalal-street-ai'
$StepNum   = 0
$Done      = @()
$Skipped   = @()

function Step($title) {
    $script:StepNum++
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor DarkGray
    Write-Host "  STEP $script:StepNum - $title" -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor DarkGray
}
function Good($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Note($m) { Write-Host "       $m" -ForegroundColor DarkGray }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Die ($m) { Write-Host ""; Write-Host "  [X] $m" -ForegroundColor Red; Write-Host ""; exit 1 }

function Confirm-Gate {
    param([string]$Prompt, [string]$Detail, [switch]$AutoOk)
    Write-Host ""
    Write-Host "  ---- IRREVERSIBLE ----" -ForegroundColor Yellow
    Write-Host "  $Detail" -ForegroundColor White
    if ($DryRun)  { Warn "DryRun: skipping"; return $false }
    if ($AutoOk)  { Good "Auto-approved (-Yes)"; return $true }
    Write-Host ""
    $a = Read-Host "  $Prompt  (type 'yes' to proceed, anything else to skip)"
    if ($a -eq 'yes') { return $true }
    Warn "Skipped by user"
    return $false
}

function Invoke-Git {
    <#
      Run git and return its combined output as plain strings.

      git uses stderr for warnings ("CRLF will be replaced by LF"), progress,
      and other non-fatal chatter. Only the exit code says whether it worked,
      so that is the single thing callers should test - via $script:GitExit.
    #>
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $out = & git @GitArgs 2>&1 | ForEach-Object { "$_" }
    $script:GitExit = $LASTEXITCODE
    return $out
}

function Get-Python {
    foreach ($c in @('python', 'python3', 'py')) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    Die "Python not found on PATH. Install Python 3.11+ and retry."
}

Clear-Host
Write-Host ""
Write-Host "  DALAL STREET AI - PUBLISH PIPELINE" -ForegroundColor White
Write-Host "  $(Get-Location)" -ForegroundColor DarkGray
if ($DryRun) { Write-Host "  DRY RUN - nothing will be committed, pushed or uploaded" -ForegroundColor Yellow }
Write-Host ""

$py = Get-Python

# =====================================================================
Step "Credential check"
# =====================================================================
if (Test-Path ".private\Config.md") {
    Write-Host ""
    Write-Host "  Five credentials were found in plaintext on this machine." -ForegroundColor Yellow
    Write-Host "  They were never committed to git, but assume they are compromised." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    2x GitHub PAT        https://github.com/settings/tokens"
    Write-Host "    Gemini API key       https://aistudio.google.com/apikey"
    Write-Host "    Groq API key         https://console.groq.com/keys"
    Write-Host "    GCS service account  GCP Console > IAM > Service Accounts"
    Write-Host ""
    if (-not $DryRun) {
        $a = Read-Host "  Have you revoked all five?  (yes / no)"
        if ($a -ne 'yes') {
            Write-Host ""
            Warn "Go do that first. Nothing else here has a deadline; this does."
            Warn "Re-run this script when done. Use -DryRun to preview meanwhile."
            exit 1
        }
    }
    Good "Confirmed revoked"
} else {
    Note "No .private\Config.md found - nothing to revoke"
}

# =====================================================================
Step "Install dev dependencies"
# =====================================================================
& $py -m pip install -q -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { Die "pip install failed. Check requirements-dev.txt" }
Good "Dev dependencies installed"
$Done += "dev deps"

# =====================================================================
Step "Preflight safety check"
# =====================================================================
& "$PSScriptRoot\preflight.ps1"
if ($LASTEXITCODE -ne 0) { Die "Preflight failed. Fix the blockers above, then re-run." }
Good "Preflight passed"
$Done += "preflight"

# =====================================================================
Step "Normalise line endings"
# =====================================================================
if ($DryRun) { Warn "DryRun: skipping git add --renormalize" ; $Skipped += "renormalize" }
else {
    # Stage deletions FIRST. `--renormalize` re-reads every tracked file to
    # recompute line endings, and aborts on the first one that is in the index
    # but missing from disk. Around 60 files were archived out of the tree
    # during cleanup, so git must be told they are gone before it can walk the
    # index cleanly.
    $missing = Invoke-Git ls-files --deleted
    if ($missing) {
        $n = ($missing | Measure-Object -Line).Lines
        Note "$n tracked file(s) no longer on disk (archived during cleanup) - staging the deletions"
        Invoke-Git add -A | Out-Null
        if ($GitExit -ne 0) { Die "git add -A failed" }
    }

    $out = Invoke-Git add --renormalize .
    $crlf = ($out | Select-String 'CRLF will be replaced' | Measure-Object).Count
    if ($crlf -gt 0) { Note "$crlf file(s) converted from CRLF to LF (this is the point of the step)" }

    if ($GitExit -ne 0) {
        if ("$out" -match 'index\.lock') {
            Write-Host ""
            Warn "git is blocked by a stale lock file: .git\index.lock"
            Warn "This is left behind when a git process is killed mid-write."
            Write-Host ""
            $running = Get-Process git -ErrorAction SilentlyContinue
            if ($running) {
                Die "A git process is still running (PID $($running.Id -join ', ')). Wait for it, or close your editor, then re-run."
            }
            $a = Read-Host "  No git process is running, so the lock is stale. Delete it?  (yes / no)"
            if ($a -eq 'yes') {
                Remove-Item ".git\index.lock" -Force
                Good "Stale lock removed"
                Invoke-Git add --renormalize . | Out-Null
                if ($GitExit -ne 0) { Die "git still failing after clearing the lock. Run 'git status' and see what it says." }
            } else {
                Die "Cannot continue while the lock exists. Remove-Item .git\index.lock, then re-run."
            }
        } else {
            Die "git add --renormalize failed: $out"
        }
    }
    Good "Line endings normalised to LF"
    $Done += "renormalize"
}

# =====================================================================
Step "Build and verify a sample database"
# =====================================================================
if (Test-Path "App\database\stock_market_new.db") {
    Note "Full database present - verifying it directly"
    & $py scripts\verify_db.py
    if ($LASTEXITCODE -ne 0) { Warn "verify_db reported problems (not blocking publish)" }
    else { Good "Database verified" }
} else {
    Note "No database found - building a sample"
    & $py scripts\bootstrap_db.py --sample
    if ($LASTEXITCODE -ne 0) { Warn "Could not build a sample database - see docs\REBUILD.md" }
    else {
        & $py scripts\verify_db.py
        if ($LASTEXITCODE -eq 0) { Good "Sample database built and verified" }
    }
}
$Done += "database check"

# =====================================================================
Step "Container build test"
# =====================================================================
if ($SkipDocker) { Warn "Skipped (-SkipDocker)"; $Skipped += "docker" }
elseif (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Warn "Docker not installed - skipping. Test manually before announcing."
    $Skipped += "docker"
} else {
    Note "Building containers (this is the one thing never verified end to end)..."
    docker compose build 2>&1 | Select-Object -Last 20 | ForEach-Object { Note $_ }
    if ($LASTEXITCODE -ne 0) {
        Warn "Container build FAILED."
        Warn "Most likely cause: App\frontend\Dockerfile installs TA-Lib and relies on a"
        Warn "prebuilt wheel existing. This will hit every first-time cloner - worth fixing."
        $Skipped += "docker (failed)"
    } else {
        Good "Containers build cleanly"
        $Done += "docker build"
    }
}

# =====================================================================
Step "Commit"
# =====================================================================
$changes = (Invoke-Git status --porcelain | Measure-Object -Line).Lines
Note "$changes changed or new paths"

if ($changes -eq 0) {
    Note "Nothing to commit"
} else {
    $msg = @"
Open-source release: docs, tests, CI, data tooling

- Add README, LICENSE (Apache-2.0), CONTRIBUTING, SECURITY, CODE_OF_CONDUCT
- Add 165 tests covering ticker resolver, query builder and config
- Add GitHub Actions CI with gitleaks secret scanning
- Track the full ingestion pipeline (previously gitignored by pattern)
- Add bootstrap_db, verify_db, export_dataset, build_resolution_benchmark
- Replace all hardcoded paths with env-driven configuration
- Harden API: admin auth, configurable CORS, no debug-reload default
- Generate the LLM system prompt from live schema introspection
- Remove personal content and deployment transcripts
"@
    if (Confirm-Gate -Prompt "Commit these $changes changes?" `
                     -Detail "Creates a local commit. Not pushed yet - still undoable with 'git reset HEAD~1'." `
                     -AutoOk:$Yes) {
        Invoke-Git add -A | Out-Null
        if ($GitExit -ne 0) { Die "git add -A failed" }
        $out = Invoke-Git commit -m $msg
        if ($GitExit -ne 0) { Die "Commit failed:`n$($out -join "`n")" }
        $out | Select-Object -First 3 | ForEach-Object { Note $_ }
        Good "Committed"
        $Done += "commit"
    } else { $Skipped += "commit" }
}

# =====================================================================
Step "Push to GitHub"
# =====================================================================
$remote = (Invoke-Git remote get-url origin) -join ''
if ($remote -match 'dalal-street-ai-(\.git)?$') {
    Write-Host ""
    Warn "Your remote still uses the OLD repo name (trailing hyphen):"
    Warn "  $remote"
    Warn "Every file now points at '$RepoName' without the hyphen."
    Write-Host ""
    Write-Host "  Rename it first:  GitHub > Settings > General > Repository name > $RepoName" -ForegroundColor White
    Write-Host ""
    if (-not $DryRun) {
        $a = Read-Host "  Have you renamed it?  (yes / no)"
        if ($a -eq 'yes') {
            Invoke-Git remote set-url origin "https://github.com/$RepoOwner/$RepoName.git" | Out-Null
            Good "Remote updated to https://github.com/$RepoOwner/$RepoName.git"
        } else {
            Warn "Skipping push. Rename the repo, then re-run this script."
            $Skipped += "push (repo not renamed)"
        }
    }
}

if ($Skipped -notcontains "push (repo not renamed)") {
    $branch = (Invoke-Git rev-parse --abbrev-ref HEAD) -join ''
    if (Confirm-Gate -Prompt "Push '$branch' to GitHub?" `
                     -Detail "This makes everything PUBLIC and cannot be undone. Repo: $RepoOwner/$RepoName") {
        # git push writes all of its progress to stderr. Show it, but judge
        # success only by the exit code.
        $out = Invoke-Git push origin $branch
        $out | ForEach-Object { Note $_ }
        if ($GitExit -ne 0) { Die "Push failed. See the output above." }
        Good "Pushed to https://github.com/$RepoOwner/$RepoName"
        $Done += "push"
    } else { $Skipped += "push" }
}

# =====================================================================
Step "Build the Kaggle datasets"
# =====================================================================
if ($SkipDataset) { Warn "Skipped (-SkipDataset)"; $Skipped += "dataset" }
else {
    Write-Host ""
    Write-Host "  Tier choice:" -ForegroundColor White
    Write-Host "    core    ~80K rows   NSE corporate filings and ticker lineage only" -ForegroundColor DarkGray
    Write-Host "    market  ~18M rows   + price history, indices, ETFs, FII/DII" -ForegroundColor DarkGray
    Write-Host "    full    ~18M rows   + screener.in fundamentals (highest redistribution risk)" -ForegroundColor DarkGray
    Write-Host "    skip                build nothing" -ForegroundColor DarkGray
    Write-Host ""
    Note "Reasoning and trade-offs: docs\KAGGLE.md"

    $tier = if ($DryRun) { 'skip' } else { Read-Host "  Which tier?  (core / market / full / skip)" }

    if ($tier -in @('core', 'market', 'full')) {
        $args_ = @('scripts\export_dataset.py', '--tier', $tier, '--format', 'both', '--compress', '--force')
        if ($tier -eq 'full') { $args_ += '--i-understand-fundamentals-are-scraped' }
        Note "Exporting (this streams millions of rows - be patient)..."
        & $py @args_
        if ($LASTEXITCODE -ne 0) { Warn "Export failed" }
        else {
            Good "Dataset exported to dist\dataset"
            $Done += "dataset export ($tier)"

            Note "Building the ticker-resolution benchmark..."
            & $py scripts\build_resolution_benchmark.py --per-category 500 --output dist\benchmark
            if ($LASTEXITCODE -eq 0) { Good "Benchmark built at dist\benchmark"; $Done += "benchmark" }
            else { Warn "Benchmark build failed" }
        }
    } else { Warn "Dataset build skipped"; $Skipped += "dataset" }
}

# =====================================================================
Step "Summary"
# =====================================================================
Write-Host ""
Write-Host "  COMPLETED:" -ForegroundColor Green
if ($Done.Count -eq 0) { Write-Host "    (nothing)" -ForegroundColor DarkGray }
foreach ($d in $Done) { Write-Host "    - $d" -ForegroundColor Green }

if ($Skipped.Count -gt 0) {
    Write-Host ""
    Write-Host "  SKIPPED:" -ForegroundColor Yellow
    foreach ($s in $Skipped) { Write-Host "    - $s" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host ("=" * 70) -ForegroundColor DarkGray
Write-Host "  MANUAL STEPS REMAINING (web UI only - no script can do these)" -ForegroundColor Cyan
Write-Host ("=" * 70) -ForegroundColor DarkGray
Write-Host ""
Write-Host "  GitHub  https://github.com/$RepoOwner/$RepoName" -ForegroundColor White
Write-Host "    Can be done any time:"
Write-Host "      - Settings > General > Features > tick Discussions"
Write-Host "        (the issue-template links 404 without it)"
Write-Host "      - Issues > Labels > create: data, ticker-resolver, dependencies, ci"
Write-Host "        (referenced by the issue forms, silently dropped if missing)"
Write-Host "      - Settings > General > add description and topics:"
Write-Host "        python nse indian-stock-market llm gemini streamlit fastapi sqlite finance"
Write-Host ""
Write-Host "    Only AFTER the push (Actions is empty until ci.yml reaches GitHub):"
Write-Host "      - Actions tab > a 'CI' run should appear within ~30s and go green"
Write-Host "      - If it says 'Get started with GitHub Actions', the push has not landed yet"
Write-Host ""
if ($Done -match 'dataset export') {
    Write-Host "  Kaggle  https://www.kaggle.com/datasets" -ForegroundColor White
    Write-Host "    5. pip install kaggle, put kaggle.json in %USERPROFILE%\.kaggle\"
    Write-Host "    6. cd dist\dataset ; kaggle datasets init -p ."
    Write-Host "       Edit dataset-metadata.json - set licenses to [{`"name`": `"other`"}]"
    Write-Host "       NOT CC0 or CC-BY. You do not hold those rights to grant."
    Write-Host "    7. kaggle datasets create -p . --dir-mode zip"
    Write-Host "    8. Repeat for dist\benchmark with licenses [{`"name`": `"CC-BY-4.0`"}]"
    Write-Host "       (the labelling is your own work, so CC-BY is correct there)"
    Write-Host "    9. Add both dataset URLs to the Datasets section of README.md, commit, push"
    Write-Host ""
}
Write-Host "  Cleanup" -ForegroundColor White
Write-Host "    Remove-Item PUBLISH_RUNBOOK.md, publish.ps1, preflight.ps1"
Write-Host "    git add -A ; git commit -m `"Remove publish scaffolding`" ; git push"
Write-Host ""
Write-Host "    Keep .private\ - it is gitignored and holds your originals."
Write-Host ""
