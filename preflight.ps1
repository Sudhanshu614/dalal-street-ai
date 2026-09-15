<#
.SYNOPSIS
    Read-only publish safety check for Dalal Street AI.

.DESCRIPTION
    Verifies the repository is safe to make public. Changes nothing.
    Exits 0 if every hard check passes, 1 otherwise.

.EXAMPLE
    .\preflight.ps1
    .\preflight.ps1 -Strict      # warnings also fail
    .\preflight.ps1 -SkipTests   # faster, skips pytest
#>

[CmdletBinding()]
param(
    [switch]$Strict,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'
Set-Location -Path $PSScriptRoot

$script:Pass = 0; $script:Fail = 0; $script:Warn = 0
$script:Failures = @(); $script:Warnings = @()

function Section($t) { Write-Host ""; Write-Host ("=" * 70) -ForegroundColor DarkGray; Write-Host $t -ForegroundColor Cyan; Write-Host ("=" * 70) -ForegroundColor DarkGray }
function Ok      ($m) { $script:Pass++; Write-Host "  [PASS] $m" -ForegroundColor Green }
function Bad     ($m) { $script:Fail++; $script:Failures += $m; Write-Host "  [FAIL] $m" -ForegroundColor Red }
function Caution ($m) { $script:Warn++; $script:Warnings += $m; Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Info    ($m) { Write-Host "         $m" -ForegroundColor DarkGray }

# Directories that never ship. Used by every file scan below.
$Excluded = @('\.git\', '\.venv\', '__pycache__', '\.private\', '\extra\',
              '\logs\', '\cache\', '\.trae\', '\App\database\', '\dist\', '\node_modules\')

function Get-PublishableFiles {
    param([string[]]$Include = @('*.py', '*.md', '*.yml', '*.yaml', '*.ps1', '*.sh', '*.toml', '*.ini', '*.txt', '*.json', 'Dockerfile*', '*.sql', '*.cfg'))
    Get-ChildItem -Path . -Recurse -File -Include $Include -ErrorAction SilentlyContinue |
        Where-Object {
            $p = $_.FullName
            $keep = $true
            foreach ($x in $Excluded) { if ($p -match [regex]::Escape($x)) { $keep = $false; break } }
            $keep
        }
}

Write-Host ""
Write-Host "  Dalal Street AI - publish preflight" -ForegroundColor White
Write-Host "  $(Get-Location)" -ForegroundColor DarkGray

# ---------------------------------------------------------------- toolchain
Section "1. Toolchain"

$py = $null
foreach ($c in @('python', 'python3', 'py')) {
    $r = Get-Command $c -ErrorAction SilentlyContinue
    if ($r) { $py = $c; break }
}
if ($py) {
    $v = & $py --version 2>&1
    Ok "Python found: $v (command: $py)"
    if ("$v" -match '(\d+)\.(\d+)') {
        if ([int]$Matches[1] -lt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -lt 11)) {
            Caution "Python 3.11+ recommended; you have $v"
        }
    }
} else {
    Bad "Python not found on PATH. Install 3.11+ and retry."
}

if (Get-Command git -ErrorAction SilentlyContinue) {
    Ok "git found: $((git --version) -replace 'git version ','')"
} else {
    Bad "git not found on PATH."
}

if (Get-Command docker -ErrorAction SilentlyContinue) { Ok "docker found" }
else { Caution "docker not found - you will not be able to test the container build" }

# ---------------------------------------------------------------- git state
Section "2. Repository state"

if (Test-Path ".git") {
    Ok "Git repository present"

    # A stale index.lock blocks every write operation. It is left behind when a
    # git process is killed mid-write, and git will not clear it on its own.
    if (Test-Path ".git\index.lock") {
        $lockAge = (Get-Date) - (Get-Item ".git\index.lock").LastWriteTime
        $running = Get-Process git -ErrorAction SilentlyContinue
        if ($running) {
            Bad "git/index.lock exists AND a git process is running (PID $($running.Id -join ', ')). Wait for it to finish, or close your editor."
        } else {
            Bad ("git/index.lock exists ({0:N0} minutes old) with no git process running - it is stale. Delete it: Remove-Item .git\index.lock" -f $lockAge.TotalMinutes)
        }
    } else {
        Ok "No stale git lock"
    }
    $remote = (git remote get-url origin 2>$null)
    if ($remote) {
        Info "origin: $remote"
        if ($remote -match 'dalal-street-ai-(\.git)?$') {
            Caution "Remote still uses the OLD name with a trailing hyphen. Rename the repo on GitHub to 'dalal-street-ai', then: git remote set-url origin https://github.com/Sudhanshu614/dalal-street-ai.git"
        } else { Ok "Remote name looks correct" }
    } else { Bad "No 'origin' remote configured" }

    $branch = (git rev-parse --abbrev-ref HEAD 2>$null)
    Info "branch: $branch"
} else {
    Bad "Not a git repository"
}

if (Test-Path ".gitattributes") {
    if ((Get-Content ".gitattributes" -Raw) -match 'text=auto') { Ok ".gitattributes sets line-ending normalisation" }
    else { Caution ".gitattributes exists but has no 'text=auto' rule" }
} else { Bad ".gitattributes missing" }

# ---------------------------------------------------------------- required files
Section "3. Required files"

$required = @(
    'README.md', 'LICENSE', 'CONTRIBUTING.md', 'SECURITY.md', 'CODE_OF_CONDUCT.md',
    '.gitignore', '.env.example', 'requirements.txt', 'requirements-dev.txt',
    'pytest.ini', 'docker-compose.yml',
    'docs\DATA.md', 'docs\REBUILD.md', 'docs\KAGGLE.md', 'docs\ROADMAP.md', 'docs\FILE_INVENTORY.md',
    'scripts\bootstrap_db.py', 'scripts\verify_db.py', 'scripts\export_dataset.py', 'scripts\build_resolution_benchmark.py',
    'tests\conftest.py', 'tests\test_ticker_resolver.py', 'tests\test_generic_query_builder.py', 'tests\test_config.py',
    '.github\workflows\ci.yml', '.github\PULL_REQUEST_TEMPLATE.md', '.github\dependabot.yml',
    'App\config.py', 'App\api\server.py', 'App\frontend\streamlit_app.py',
    'App\src\data_fetcher\ticker_resolver.py', 'App\src\data_fetcher\generic_query_builder.py'
)
$missing = @()
foreach ($f in $required) { if (-not (Test-Path $f)) { $missing += $f } }
if ($missing.Count -eq 0) { Ok "All $($required.Count) required files present" }
else { Bad "Missing $($missing.Count) required file(s): $($missing -join ', ')" }

$pipelineCount = (Get-ChildItem 'App\scriptsrebuild' -Filter '*.py' -ErrorAction SilentlyContinue).Count
if ($pipelineCount -ge 40) { Ok "Ingestion pipeline present ($pipelineCount scripts in App\scriptsrebuild)" }
else { Bad "Only $pipelineCount scripts in App\scriptsrebuild - expected ~44" }

# ---------------------------------------------------------------- secrets
Section "4. Secret scan (the one that matters)"

$secretPatterns = @{
    'Google API key'        = 'AIza[0-9A-Za-z_\-]{30,}'
    'Groq API key'          = 'gsk_[A-Za-z0-9]{40,}'
    'GitHub token'          = 'gh[pousr]_[A-Za-z0-9]{30,}'
    'OpenAI key'            = 'sk-[A-Za-z0-9]{32,}'
    'Private key block'     = '-----BEGIN [A-Z ]*PRIVATE KEY-----'
    'Slack token'           = 'xox[baprs]-[A-Za-z0-9\-]{10,}'
    'AWS access key'        = 'AKIA[0-9A-Z]{16}'
}
$files = Get-PublishableFiles
Info "Scanning $($files.Count) publishable files..."
$secretHits = @()
foreach ($name in $secretPatterns.Keys) {
    $hits = $files | Select-String -Pattern $secretPatterns[$name] -ErrorAction SilentlyContinue
    foreach ($h in $hits) {
        $rel = $h.Path.Replace("$PSScriptRoot\", "")
        $secretHits += "$name in ${rel}:$($h.LineNumber)"
    }
}
if ($secretHits.Count -eq 0) { Ok "No credentials found in any publishable file" }
else { foreach ($h in $secretHits) { Bad "SECRET: $h" } }

# ---------------------------------------------------------------- gitignore
Section "5. Gitignore coverage"

$mustIgnore = @('.env', 'App\.env', '.private\Config.md', 'App\database\stock_market_new.db',
                'extra\check_db.py', 'logs\x.log', '.trae\documents\x.md', 'dist\dataset\x.csv')
$notIgnored = @()
foreach ($p in $mustIgnore) {
    git check-ignore -q $p 2>$null
    if ($LASTEXITCODE -ne 0) { $notIgnored += $p }
}
if ($notIgnored.Count -eq 0) { Ok "All sensitive paths are gitignored" }
else { foreach ($p in $notIgnored) { Bad "NOT IGNORED (would be committed): $p" } }

$mustTrack = @('tests\test_ticker_resolver.py', 'scripts\export_dataset.py',
               'App\scriptsrebuild\01_create_schema.py', '.github\workflows\ci.yml', '.env.example')
$wronglyIgnored = @()
foreach ($p in $mustTrack) {
    if (Test-Path $p) {
        git check-ignore -q $p 2>$null
        if ($LASTEXITCODE -eq 0) { $wronglyIgnored += $p }
    }
}
if ($wronglyIgnored.Count -eq 0) { Ok "Source, tests, pipeline and CI are all tracked" }
else { foreach ($p in $wronglyIgnored) { Bad "WRONGLY IGNORED (would be missing from the repo): $p" } }

# ---------------------------------------------------------------- placeholders + personal data
Section "6. Placeholders and personal data"

$placeholders = @('<your-username>', '\[MAINTAINER EMAIL\]', 'TODO: FILL', 'XXX-REPLACE')
$phHits = @()
foreach ($p in $placeholders) {
    $hits = $files | Where-Object { $_.Name -ne 'preflight.ps1' -and $_.Name -ne 'publish.ps1' } |
            Select-String -Pattern $p -ErrorAction SilentlyContinue
    foreach ($h in $hits) { $phHits += "$($h.Path.Replace("$PSScriptRoot\",'')):$($h.LineNumber)  $($h.Line.Trim())" }
}
if ($phHits.Count -eq 0) { Ok "No unfilled placeholders" }
else { foreach ($h in $phHits) { Bad "PLACEHOLDER: $h" } }

# Old infrastructure details that should not be public.
# Patterns that must never reach a public repo. The Windows account name is
# read from the environment rather than written here - hardcoding it would put
# the very string we are hunting for into a file that gets committed.
$personal = @{
    'Old VM IP (34.x)'  = '34\.45\.213\.224'
    'Old VM IP (136.x)' = '136\.115\.41\.222'
    'Internal IP'       = '10\.209\.27\.197'
    'Unix username'     = 'sudhanshubawane_work'
    'GCP project id'    = 'gen-lang-client-\d+'
}
if ($env:USERNAME -and $env:USERNAME.Length -ge 4) {
    $personal['Windows username'] = [regex]::Escape($env:USERNAME)
}
if ($env:USERPROFILE) {
    $personal['Home directory path'] = [regex]::Escape($env:USERPROFILE)
}
$pHits = @()
foreach ($name in $personal.Keys) {
    $hits = $files | Where-Object { $_.Name -ne 'preflight.ps1' } |
            Select-String -Pattern $personal[$name] -ErrorAction SilentlyContinue
    foreach ($h in $hits) { $pHits += "$name in $($h.Path.Replace("$PSScriptRoot\",'')):$($h.LineNumber)" }
}
if ($pHits.Count -eq 0) { Ok "No personal infrastructure details in publishable files" }
else { foreach ($h in $pHits) { Bad "PERSONAL DATA: $h" } }

# Hardcoded absolute paths
$absHits = $files | Where-Object { $_.Name -ne 'preflight.ps1' -and $_.Name -ne 'publish.ps1' -and $_.Name -ne 'PUBLISH_RUNBOOK.md' } |
           Select-String -Pattern '[A-Za-z]:\\\\?(Dalal Street Trae|Users\\)' -ErrorAction SilentlyContinue
if (-not $absHits) { Ok "No hardcoded absolute paths" }
else { foreach ($h in $absHits) { Bad "ABSOLUTE PATH: $($h.Path.Replace("$PSScriptRoot\",'')):$($h.LineNumber)" } }

# ---------------------------------------------------------------- syntax
Section "7. Syntax"

if ($py) {
    $pyFiles = $files | Where-Object { $_.Extension -eq '.py' }
    $bad = @()
    foreach ($f in $pyFiles) {
        & $py -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" $f.FullName 2>$null
        if ($LASTEXITCODE -ne 0) { $bad += $f.FullName.Replace("$PSScriptRoot\", "") }
    }
    if ($bad.Count -eq 0) { Ok "All $($pyFiles.Count) Python files parse" }
    else { foreach ($b in $bad) { Bad "SYNTAX ERROR: $b" } }

    $yamlFiles = $files | Where-Object { $_.Extension -in @('.yml', '.yaml') }
    & $py -c "import yaml" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $badY = @()
        foreach ($f in $yamlFiles) {
            & $py -c "import yaml,sys; yaml.safe_load(open(sys.argv[1],encoding='utf-8'))" $f.FullName 2>$null
            if ($LASTEXITCODE -ne 0) { $badY += $f.FullName.Replace("$PSScriptRoot\", "") }
        }
        if ($badY.Count -eq 0) { Ok "All $($yamlFiles.Count) YAML files parse" }
        else { foreach ($b in $badY) { Bad "INVALID YAML: $b" } }
    } else { Caution "pyyaml not installed - skipped YAML validation (pip install pyyaml)" }
}

# ---------------------------------------------------------------- tests
Section "8. Tests"

if ($SkipTests) { Caution "Skipped (-SkipTests)" }
elseif ($py) {
    & $py -m pytest --version 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Caution "pytest not installed - run: $py -m pip install -r requirements-dev.txt"
    } else {
        $out = & $py -m pytest tests/ -q 2>&1
        $line = ($out | Select-String -Pattern 'passed|failed|error' | Select-Object -Last 1)
        if ($LASTEXITCODE -eq 0) { Ok "Test suite passes - $($line -replace '\s+',' ')" }
        else {
            Bad "TEST FAILURES - $($line -replace '\s+',' ')"
            $out | Select-Object -Last 25 | ForEach-Object { Info $_ }
        }
    }
}

# ---------------------------------------------------------------- runtime config
Section "9. Runtime configuration"

if (Test-Path ".env") {
    $env_ = Get-Content ".env" -Raw
    if ($env_ -match 'GEMINI_API_KEY\s*=\s*\S+') { Ok ".env exists with a GEMINI_API_KEY set" }
    else { Caution ".env exists but GEMINI_API_KEY is empty - the backend will not start" }
    if ($env_ -match 'AIzaSyAknwecvStZs7YBYB7j2I') { Bad "Your .env still holds the OLD LEAKED Gemini key. Revoke it and generate a new one." }
} else {
    Caution ".env not found - copy .env.example to .env and add your GEMINI_API_KEY"
}

if (Test-Path "App\database\stock_market_new.db") {
    $sz = (Get-Item "App\database\stock_market_new.db").Length / 1GB
    Ok ("Database present ({0:N1} GB)" -f $sz)
} else {
    Caution "No database at App\database\stock_market_new.db - run: python scripts\bootstrap_db.py --sample"
}

if (Test-Path ".private") {
    Ok ".private\ quarantine intact (gitignored, holds your originals)"
    Info "Revoke everything listed in .private\Config.md and .private\env.backup if you have not already"
}

# ---------------------------------------------------------------- summary
Section "RESULT"

Write-Host ""
Write-Host "  Passed:   $script:Pass" -ForegroundColor Green
Write-Host "  Warnings: $script:Warn" -ForegroundColor Yellow
Write-Host "  Failed:   $script:Fail" -ForegroundColor $(if ($script:Fail -gt 0) { 'Red' } else { 'Green' })
Write-Host ""

if ($script:Fail -gt 0) {
    Write-Host "  BLOCKERS:" -ForegroundColor Red
    foreach ($f in $script:Failures) { Write-Host "    - $f" -ForegroundColor Red }
    Write-Host ""
    Write-Host "  NOT SAFE TO PUBLISH. Fix the above and re-run." -ForegroundColor Red
    Write-Host ""
    exit 1
}

if ($script:Warn -gt 0) {
    Write-Host "  Warnings (not blocking):" -ForegroundColor Yellow
    foreach ($w in $script:Warnings) { Write-Host "    - $w" -ForegroundColor Yellow }
    Write-Host ""
    if ($Strict) { Write-Host "  -Strict set: treating warnings as failure." -ForegroundColor Red; exit 1 }
}

Write-Host "  SAFE TO PUBLISH." -ForegroundColor Green
Write-Host "  Next: .\publish.ps1" -ForegroundColor White
Write-Host ""
exit 0
