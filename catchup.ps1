<#
.SYNOPSIS
    Bring the warehouse up to date in one run, then verify it.

.DESCRIPTION
    Runs the full catch-up in dependency order and logs everything:

        0. Stage the five NSE CSVs and back up the database
        1. Ticker resolution data  (master, name/symbol changes, corporate actions, IPOs)
        2. OHLCV                   (date-ranged, long)
        3. Indices and ETFs        (date-ranged, long)
        4. FII/DII                 (date-ranged)
        5. Fundamentals            (one-shot snapshot, ~2600 scrapes, longest)
        6. Returns                 (derived from OHLCV - must run after step 2)
        7. Verify

    Order matters. Ticker data must land before prices, and prices before
    returns. Steps are attempted even if an earlier one failed, because a
    partial catch-up is better than none - the summary tells you what to redo.

    Expect this to run for HOURS. Leave it alone. Everything is written to
    logs\catchup-<timestamp>.log as well as the console.

.PARAMETER From
    Start date (YYYY-MM-DD). Default: the day after the newest daily_ohlc row.

.PARAMETER To
    End date (YYYY-MM-DD). Default: today.

.PARAMETER StageFrom
    Folder holding the five manually-downloaded NSE CSVs. Defaults to your
    Downloads folder. See docs/REBUILD.md for which five and where to get them.

.PARAMETER SkipStage
    The five NSE CSVs are already in App\database\. Do not copy them.

.PARAMETER SkipBackup
    Do not copy the 3.9 GB database first. Not recommended.

.PARAMETER Only
    Run one phase:

        ticker        NSE master, name/symbol changes, corporate actions, IPOs
        ohlcv         daily_ohlc
        indices       market_indices AND market_etfs (one script fills both;
                      'etfs' is accepted as an alias for the same phase)
        fiidii        fii_dii_data
        fundamentals  fundamentals, quarterly_results, annual_financials
        returns       return columns, derived from daily_ohlc
        verify        read-only check, writes nothing

.EXAMPLE
    .\catchup.ps1                      # everything, auto date range
    .\catchup.ps1 -Only fundamentals   # resume just the scrape
    .\catchup.ps1 -From 2026-02-26 -To 2026-09-15
#>

[CmdletBinding()]
param(
    [string]$From,
    [string]$To,
    [string]$StageFrom,
    [switch]$SkipStage,
    [switch]$SkipBackup,
    # 'indices' and 'etfs' are the same phase - one script fills both the
    # market_indices and market_etfs tables. Both spellings accepted so you do
    # not have to remember which.
    [ValidateSet('ticker', 'ohlcv', 'indices', 'etfs', 'fiidii', 'fundamentals', 'returns', 'verify')]
    [string]$Only
)

if ($Only -eq 'etfs') { $Only = 'indices' }

# git and python write progress to stderr; only exit codes mean anything.
$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'
Set-Location -Path $PSScriptRoot

# The Windows console defaults to cp1252, which cannot encode the check marks,
# arrows and emoji that the pipeline scripts print. Without this, a successful
# load raises UnicodeEncodeError on the *summary line* - the data is written,
# then printing the result kills the step. Force UTF-8 for every child process.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# Where the five manually-downloaded NSE CSVs are picked up from. Defaults to
# the browser's download folder, since that is where they land. Override with
# -StageFrom, or skip staging entirely with -SkipStage if they are already in
# App\database\. Never hardcode a user-specific path here.
if (-not $StageFrom) { $StageFrom = Join-Path $env:USERPROFILE 'Downloads' }
$DbPath     = "App\database\stock_market_new.db"
$Stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'
$LogDir     = "logs"
$LogFile    = Join-Path $LogDir "catchup-$Stamp.log"
$Results    = [ordered]@{}
$StartedAt  = Get-Date

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log {
    param([string]$Message, [string]$Colour = 'Gray')
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Message
    Write-Host $line -ForegroundColor $Colour
    Add-Content -Path $LogFile -Value $line
}
function Phase($n, $title) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor DarkGray
    Write-Host "  PHASE $n - $title" -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor DarkGray
    Add-Content -Path $LogFile -Value "`n===== PHASE $n - $title ====="
}
function Good($m) { Log "OK   $m" 'Green' }
function Warn($m) { Log "WARN $m" 'Yellow' }
function Bad ($m) { Log "FAIL $m" 'Red' }

function Get-Python {
    foreach ($c in @('python', 'python3', 'py')) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    Log "Python not found on PATH." 'Red'; exit 1
}
$py = Get-Python

function Run-Step {
    <# Run a python script, stream + log its output, record pass/fail and duration. #>
    param([string]$Key, [string]$Label, [string[]]$Arguments)

    if ($Only -and $Only -ne $Key) { return }

    $t0 = Get-Date
    Log "START $Label" 'White'
    Log "      $py $($Arguments -join ' ')"

    & $py @Arguments 2>&1 | ForEach-Object {
        $text = "$_"
        Write-Host "       $text" -ForegroundColor DarkGray
        Add-Content -Path $LogFile -Value "       $text"
    }
    $code = $LASTEXITCODE
    $mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)

    if ($code -eq 0) {
        Good "$Label finished in $mins min"
        $script:Results[$Label] = "OK ($mins min)"
    } else {
        Bad "$Label exited $code after $mins min - continuing"
        $script:Results[$Label] = "FAILED after $mins min (exit $code)"
    }
}

Clear-Host
Write-Host ""
Write-Host "  DALAL STREET AI - DATABASE CATCH-UP" -ForegroundColor White
Write-Host "  $(Get-Location)" -ForegroundColor DarkGray
Write-Host "  Log: $LogFile" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  This runs for HOURS. Leave the window open." -ForegroundColor Yellow
Write-Host ""

# =====================================================================
# Work out the date range from the database itself.
# =====================================================================
if (-not (Test-Path $DbPath)) { Log "Database not found at $DbPath" 'Red'; exit 1 }

# Each time-series table has its own last date, and they drift apart. Deriving
# one range from daily_ohlc alone is wrong: after OHLCV catches up, a re-run
# would ask the indices job for "yesterday to today" while market_indices is
# still months behind. Probe every table separately.
$probe = @"
import sqlite3, datetime, json
c = sqlite3.connect('file:$($DbPath -replace '\\','/')?mode=ro', uri=True)
out = {}
for table in ('daily_ohlc', 'market_indices', 'market_etfs', 'fii_dii_data'):
    try:
        cols = [r[1] for r in c.execute(f'PRAGMA table_info({table})')]
        col = next((x for x in ('date', 'trade_date') if x in cols), None)
        if not col:
            continue
        last = c.execute(f'SELECT MAX("{col}") FROM "{table}"').fetchone()[0]
        if last:
            nxt = datetime.date.fromisoformat(str(last)[:10]) + datetime.timedelta(days=1)
            out[table] = [str(last)[:10], nxt.isoformat()]
    except Exception:
        pass
c.close()
print(json.dumps(out))
"@
$probeOut = (& $py -c $probe 2>&1) -join ''
try { $Last = $probeOut | ConvertFrom-Json } catch {
    Log "Could not read table dates from the database: $probeOut" 'Red'; exit 1
}
if (-not $To) { $To = (Get-Date -Format 'yyyy-MM-dd') }

function Get-Range {
    <# Start date for one table: the explicit -From if given, else the day
       after that table's own newest row. #>
    param([string]$Table)
    if ($From) { return $From }
    if ($Last.$Table) { return $Last.$Table[1] }
    return $To
}

$OhlcFrom    = Get-Range 'daily_ohlc'
$IndicesFrom = Get-Range 'market_indices'
$FiiFrom     = Get-Range 'fii_dii_data'

Write-Host ""
Log "Newest row per table, and the range each phase will run:" 'White'
foreach ($t in @('daily_ohlc', 'market_indices', 'market_etfs', 'fii_dii_data')) {
    if ($Last.$t) { Log ("  {0,-16} last {1}" -f $t, $Last.$t[0]) }
    else { Log ("  {0,-16} (no date column found)" -f $t) 'Yellow' }
}
Log "  OHLCV        $OhlcFrom -> $To"
Log "  Indices/ETFs $IndicesFrom -> $To"
Log "  FII/DII      $FiiFrom -> $To"

$idxSpan = [math]::Round((([datetime]$To) - ([datetime]$IndicesFrom)).TotalDays)
if ($idxSpan -gt 30) {
    Log "Indices/ETFs is $idxSpan days behind - that phase will take a while." 'Yellow'
}
if ($Only) { Log "Single phase requested: $Only" 'Yellow' }

# =====================================================================
Phase 0 "Stage CSVs and back up"
# =====================================================================
if (-not $Only) {
    if (-not $SkipStage) {
        # NSE ships the securities list as EQUITY_L.csv; the pipeline wants
        # stock_master.csv. The other four keep their names (two are matched
        # by glob, so the date suffix is fine).
        $map = @{
            'EQUITY_L.csv'    = 'stock_master.csv'
            'namechange.csv'  = 'namechange.csv'
            'symbolchange.csv' = 'symbolchange.csv'
        }
        $staged = 0
        foreach ($src in $map.Keys) {
            $s = Join-Path $StageFrom $src
            if (Test-Path $s) {
                Copy-Item $s (Join-Path "App\database" $map[$src]) -Force
                Good "staged $src -> $($map[$src])"; $staged++
            } else { Warn "not found in uploads: $src" }
        }
        foreach ($pattern in @('CF-CA-equities-*.csv', 'IPO-PastIssue-*.csv')) {
            Get-ChildItem -Path $StageFrom -Filter $pattern -ErrorAction SilentlyContinue |
                ForEach-Object {
                    Copy-Item $_.FullName (Join-Path "App\database" $_.Name) -Force
                    Good "staged $($_.Name)"; $staged++
                }
        }
        if ($staged -lt 5) {
            Warn "Only $staged of 5 CSVs staged. Missing ones mean stale ticker data."
            $a = Read-Host "  Continue anyway? (yes / no)"
            if ($a -ne 'yes') { exit 1 }
        }
    } else { Warn "Staging skipped (-SkipStage)" }

    if (-not $SkipBackup) {
        $backup = "App\database\stock_market_new.backup-$Stamp.db"
        Log "Backing up 3.9 GB database - a few minutes..." 'White'
        Copy-Item $DbPath $backup -Force
        Good "backup at $backup"
        Log "      (delete it once you are happy with the result)"
    } else { Warn "Backup skipped (-SkipBackup)" }
}

# =====================================================================
Phase 1 "Ticker resolution data"
# =====================================================================
# Must be first. Without it, seven months of new listings look like unknown
# tickers to the bhavcopy loader and the demerger correlator invents matches.
Run-Step 'ticker' 'NSE master / name / symbol / corporate actions' `
    @('App\scriptsrebuild\04_daily_nse_update.py')
Run-Step 'ticker' 'IPO import' `
    @('App\scriptsrebuild\11_import_ipo_data.py')

# =====================================================================
Phase 2 "OHLCV"
# =====================================================================
Run-Step 'ohlcv' "Daily OHLCV $OhlcFrom -> $To" `
    @('App\DAILY_RUNNER.py', '--start', $OhlcFrom, '--end', $To)

# =====================================================================
Phase 3 "Indices and ETFs  (fills market_indices + market_etfs)"
# =====================================================================
Run-Step 'indices' "Indices and ETFs $IndicesFrom -> $To" `
    @('App\scripts\rebuild\10_daily_update_indices_etfs.py', '--start-date', $IndicesFrom, '--end-date', $To)

# =====================================================================
Phase 4 "FII / DII flows"
# =====================================================================
Run-Step 'fiidii' "FII/DII $FiiFrom -> $To" `
    @('App\scriptsrebuild\14_scrape_fii_dii_data.py', '--start-date', $FiiFrom, '--end-date', $To)

# =====================================================================
Phase 5 "Fundamentals"
# =====================================================================
# Not a time series. `fundamentals` is one current row per company, and the
# quarterly/annual tables are keyed by period - so a single scrape now picks
# up every period that was missed. This is the slowest phase: ~2600 rate-
# limited requests to screener.in.
Run-Step 'fundamentals' 'Sync newly listed companies' `
    @('App\scriptsrebuild\10_sync_new_companies.py')
Run-Step 'fundamentals' 'Fundamentals + quarterly + annual (--all)' `
    @('App\scripts\rebuild\unified_data_updater.py', '--all')

# =====================================================================
Phase 6 "Returns"
# =====================================================================
# Derived from daily_ohlc, so it has to come after phase 2.
Run-Step 'returns' 'Calculate returns' `
    @('App\scriptsrebuild\09_calculate_returns.py')

# =====================================================================
Phase 7 "Verify"
# =====================================================================
Run-Step 'verify' 'Database verification' @('scripts\verify_db.py')

# =====================================================================
# Summary
# =====================================================================
$elapsed = [math]::Round(((Get-Date) - $StartedAt).TotalMinutes, 1)
Write-Host ""
Write-Host ("=" * 72) -ForegroundColor DarkGray
Write-Host "  SUMMARY  -  total $elapsed minutes" -ForegroundColor Cyan
Write-Host ("=" * 72) -ForegroundColor DarkGray
Write-Host ""

$failed = 0
foreach ($k in $Results.Keys) {
    $v = $Results[$k]
    if ($v -like 'OK*') { Write-Host ("  {0,-52} {1}" -f $k, $v) -ForegroundColor Green }
    else { Write-Host ("  {0,-52} {1}" -f $k, $v) -ForegroundColor Red; $failed++ }
}

Write-Host ""
Write-Host "  Full log: $LogFile" -ForegroundColor DarkGray
Write-Host ""

if ($failed -gt 0) {
    Write-Host "  $failed step(s) failed. Re-run just those:" -ForegroundColor Yellow
    Write-Host "    .\catchup.ps1 -Only fundamentals -SkipStage -SkipBackup" -ForegroundColor White
    Write-Host "    .\catchup.ps1 -Only ohlcv -SkipStage -SkipBackup" -ForegroundColor White
    Write-Host ""
    Write-Host "  If the fundamentals scrape died partway, resume from where it stopped:" -ForegroundColor Yellow
    Write-Host "    python App\scripts\rebuild\unified_data_updater.py --all --start-from SYMBOL" -ForegroundColor White
} else {
    Write-Host "  All phases completed." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Check the verification output above, then build the datasets:" -ForegroundColor White
    Write-Host "    .\publish.ps1 -SkipDocker" -ForegroundColor White
}
Write-Host ""
