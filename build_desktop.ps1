param(
    [switch]$Windowed
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is required to build the frontend. Install Node.js first."
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "python is required to build the desktop package."
}

Push-Location frontend
try {
    if (-not (Test-Path node_modules)) {
        Invoke-Native npm ci
    } else {
        Write-Host "Using existing frontend\\node_modules"
    }
    Invoke-Native npm run build
} finally {
    Pop-Location
}

if (-not (Test-Path "frontend\dist\index.html")) {
    throw "frontend/dist/index.html was not produced."
}

$Venv = Join-Path $Root ".venv-desktop"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$VenvPip = Join-Path $Venv "Scripts\pip.exe"
if ((Test-Path $Venv) -and (-not (Test-Path $VenvPython) -or -not (Test-Path $VenvPip))) {
    Write-Host "Removing incomplete .venv-desktop"
    Remove-Item -LiteralPath $Venv -Recurse -Force
}
if (-not (Test-Path $VenvPython)) {
    Invoke-Native python -m venv $Venv
}
Invoke-Native $VenvPython -m pip --version
Invoke-Native $VenvPython -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
if ($Windowed) {
    Invoke-Native $VenvPython -m pip install --disable-pip-version-check "pywebview>=5,<6"
}

$pyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name", "DeepResearchAgent",
    "--add-data", "frontend\dist;frontend\dist",
    "--hidden-import", "aiosqlite",
    "--hidden-import", "asyncpg",
    "--collect-submodules", "uvicorn",
    "--collect-submodules", "sqlalchemy.dialects.sqlite"
)

if ($Windowed) {
    $pyInstallerArgs += @("--windowed", "--collect-submodules", "webview")
} else {
    $pyInstallerArgs += @("--console", "--exclude-module", "webview")
}

$pyInstallerArgs += "deep_research\desktop.py"
Invoke-Native $VenvPython -m PyInstaller @pyInstallerArgs

$Exe = Join-Path $Root "dist\DeepResearchAgent\DeepResearchAgent.exe"
if (-not (Test-Path $Exe)) {
    throw "PyInstaller finished but $Exe was not found."
}

Write-Host "Built: dist\DeepResearchAgent\DeepResearchAgent.exe"
if (-not $Windowed) {
    Write-Host "Run it from a terminal. It will print the local URL and keep the backend alive."
}
