param([switch]$NoAutostart, [switch]$NoLaunch)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "LiquidGlassSensors"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "Requesting administrator rights..."
    $extra = @()
    if ($NoAutostart) { $extra += "-NoAutostart" }
    if ($NoLaunch) { $extra += "-NoLaunch" }
    Start-Process powershell -Verb RunAs -ArgumentList (@(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
    ) + $extra)
    exit
}

Write-Host "== Liquid Glass Sensors: setup ==" -ForegroundColor Cyan

# 1. Python -----------------------------------------------------------------
$python = $null
foreach ($candidate in @("py -3", "python")) {
    $parts = $candidate.Split(" ")
    try {
        $version = & $parts[0] $parts[1..($parts.Length - 1)] --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -match "3\.(1[1-9]|[2-9][0-9])") {
            $python = $candidate
            break
        }
    } catch { }
}
if (-not $python) {
    throw "Python 3.11+ was not found. Install it from https://python.org and run setup again."
}
Write-Host "Python: $(& $python.Split(' ')[0] --version)"

# 2. Virtual environment ----------------------------------------------------
$venv = Join-Path $root ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    & $python.Split(" ")[0] -m venv $venv
}
& $venvPython -m pip install --upgrade pip --quiet
Write-Host "Installing dependencies..."
& $venvPython -m pip install -r (Join-Path $root "requirements.txt") --quiet

# 3. PawnIO kernel driver (needed for CPU temperature and fan speed) --------
$driverInstalled = $false
try {
    $drivers = & pnputil /enum-drivers 2>$null | Out-String
    $driverInstalled = $drivers -match "pawnio"
} catch { }
if (-not $driverInstalled) {
    $installer = Join-Path $root "vendor\PawnIO_setup.exe"
    if (Test-Path $installer) {
        Write-Host "Installing PawnIO driver..."
        $p = Start-Process $installer -ArgumentList "-install", "-silent" -Wait -PassThru
        Write-Host "PawnIO installer exit code: $($p.ExitCode)"
    } else {
        Write-Warning "vendor\PawnIO_setup.exe not found, CPU sensors may stay unavailable."
    }
} else {
    Write-Host "PawnIO driver already installed."
}

# 4. Autostart task (runs elevated at logon) --------------------------------
if (-not $NoAutostart) {
    Write-Host "Registering autostart task..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "autostart.ps1") -Mode enable
}

# 5. Launch -----------------------------------------------------------------
if (-not $NoLaunch) {
    Write-Host "Starting widget..."
    Start-Process (Join-Path $venv "Scripts\pythonw.exe") -ArgumentList "`"$(Join-Path $root 'main.py')`"" -WorkingDirectory $root
}

Write-Host "Done. The widget sits on the desktop; right-click it for settings." -ForegroundColor Green
