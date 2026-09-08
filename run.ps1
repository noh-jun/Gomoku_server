<#
.SYNOPSIS
    Gomoku server launcher (Windows / PowerShell).

.DESCRIPTION
    Creates .venv on first run, installs requirements.txt when needed and
    then starts the server (Tkinter management GUI by default).

.EXAMPLE
    .\run.ps1
    Opens the server management GUI.

.EXAMPLE
    .\run.ps1 -BoardSize 19 -NoGui
    Starts a headless 19 x 19 server.

.EXAMPLE
    .\run.ps1 -Test
    Runs the pytest suite instead of the server.

.EXAMPLE
    .\run.ps1 -- --host 127.0.0.1 --port 9000
    Everything after -- is passed to server.py unchanged.
#>
[CmdletBinding()]
param(
    [int]$Port,
    [string]$BindHost,
    [ValidateSet(15, 19)]
    [int]$BoardSize,
    [switch]$NoGui,
    [switch]$Test,
    [switch]$Install,
    [switch]$Recreate,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Extra
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvDir = Join-Path $root ".venv"
$venvPy = Join-Path $venvDir "Scripts\python.exe"

function Find-BasePython {
    # The Windows Store python.exe stub is skipped by the version probe.
    $candidates = @(
        @{ Exe = "py"; Pre = @("-3") },
        @{ Exe = "python"; Pre = @() },
        @{ Exe = "python3"; Pre = @() }
    )
    foreach ($candidate in $candidates) {
        $found = Get-Command $candidate.Exe -ErrorAction SilentlyContinue
        if ($null -eq $found) { continue }
        $probeArgs = $candidate.Pre + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")
        $version = & $candidate.Exe @probeArgs 2>$null
        if ($LASTEXITCODE -ne 0) { continue }
        if (-not $version) { continue }
        $parts = ("$version").Trim().Split(".")
        if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 11) {
            return $candidate
        }
    }
    return $null
}

if ($Recreate -and (Test-Path $venvDir)) {
    Write-Host "[run] Removing the existing .venv ..."
    Remove-Item -Recurse -Force $venvDir
}

$needsInstall = $false

if (-not (Test-Path $venvPy)) {
    $base = Find-BasePython
    if ($null -eq $base) {
        Write-Error "No Python 3.11+ launcher found. Install Python first."
        exit 1
    }
    Write-Host "[run] Creating virtual environment in .venv ..."
    & $base.Exe @($base.Pre + @("-m", "venv", $venvDir))
    if (-not (Test-Path $venvPy)) {
        Write-Error "Failed to create .venv"
        exit 1
    }
    $needsInstall = $true
}

& $venvPy -c "import fastapi, uvicorn, pytest, httpx" 2>$null
if ($LASTEXITCODE -ne 0) { $needsInstall = $true }

if ($needsInstall -or $Install) {
    Write-Host "[run] Installing requirements ..."
    & $venvPy -m pip install --quiet --upgrade pip
    & $venvPy -m pip install -r (Join-Path $root "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install failed"
        exit 1
    }
}

if ($Install -and -not ($Test -or $NoGui -or $Port -or $BindHost -or $BoardSize)) {
    Write-Host "[run] Dependencies are ready."
    exit 0
}

if ($Test) {
    $testArgs = @()
    if ($Extra) { $testArgs = $Extra }
    Write-Host "[run] python -m pytest $testArgs"
    & $venvPy -m pytest @testArgs
    exit $LASTEXITCODE
}

$serverArgs = @()
if ($BindHost) { $serverArgs += @("--host", $BindHost) }
if ($Port) { $serverArgs += @("--port", "$Port") }
if ($BoardSize) { $serverArgs += @("--board-size", "$BoardSize") }
if ($NoGui) { $serverArgs += "--no-gui" }
if ($Extra) { $serverArgs += $Extra }

Write-Host "[run] python server.py $serverArgs"
& $venvPy (Join-Path $root "server.py") @serverArgs
exit $LASTEXITCODE
