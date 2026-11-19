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

# If UAC elevated with another administrator account, the autostart task is
# registered for that account instead of the desktop user.
$interactiveUser = $null
try {
    $interactiveUser = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).UserName
} catch { }
if ($interactiveUser -and $interactiveUser -notlike ("*\" + $env:USERNAME)) {
    Write-Warning "Setup runs as '$env:USERNAME', but the desktop user is '$interactiveUser'."
    Write-Warning "Autostart (if enabled) will be registered for '$env:USERNAME'."
}

# 0. Files from the release zip carry the "downloaded from the internet" mark,
#    which blocks the .NET sensor library. Clear it up front.
$vendor = Join-Path $root "vendor"
if (Test-Path $vendor) {
    Get-ChildItem $vendor -Recurse -File -ErrorAction SilentlyContinue | Unblock-File
}

# 1. Python -----------------------------------------------------------------
function Find-Python {
    foreach ($candidate in @(@("py", "-3"), @("python"))) {
        try {
            $cmdArgs = @()
            if ($candidate.Length -gt 1) {
                $cmdArgs = @($candidate[1..($candidate.Length - 1)])
            }
            $out = & $candidate[0] @cmdArgs --version 2>$null
            if ($LASTEXITCODE -eq 0 -and "$out" -match "3\.(1[1-9]|[2-9][0-9])") {
                return [pscustomobject]@{ Exe = $candidate[0]; Args = $cmdArgs }
            }
        } catch { }
    }

    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        $env:ProgramFiles,
        "C:\"
    )
    foreach ($rootDir in $roots) {
        if (-not $rootDir -or -not (Test-Path $rootDir)) { continue }
        $dirs = Get-ChildItem $rootDir -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
        foreach ($dir in $dirs) {
            $exe = Join-Path $dir.FullName "python.exe"
            if (-not (Test-Path $exe)) { continue }
            $out = & $exe --version 2>$null
            if ($LASTEXITCODE -eq 0 -and "$out" -match "3\.(1[1-9]|[2-9][0-9])") {
                return [pscustomobject]@{ Exe = $exe; Args = @() }
            }
        }
    }
    return $null
}

$python = Find-Python
if (-not $python -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "Python 3.11+ not found. Installing Python 3.13 with winget..."
    & winget install --id Python.Python.3.13 --exact --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [System.Environment]::GetEnvironmentVariable("Path", "User")
    $python = Find-Python
}
if (-not $python) {
    $pyVersion = "3.13.7"
    $pyUrl = "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-amd64.exe"
    $pyInstaller = Join-Path $env:TEMP "python-$pyVersion-amd64.exe"
    Write-Host "Downloading Python $pyVersion from python.org..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $pyUrl -OutFile $pyInstaller -UseBasicParsing
        Write-Host "Installing Python $pyVersion (this may take a minute)..."
        $proc = Start-Process -FilePath $pyInstaller -ArgumentList @(
            "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0", "Include_launcher=1"
        ) -Wait -PassThru
        Write-Host "Python installer exit code: $($proc.ExitCode)"
    } catch {
        Write-Warning "Could not download or run the Python installer: $_"
    } finally {
        if (Test-Path -LiteralPath $pyInstaller) {
            try {
                Remove-Item -LiteralPath $pyInstaller -Force -ErrorAction Stop
            } catch {
                # The Python bootstrapper may already have removed its own file.
            }
        }
    }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [System.Environment]::GetEnvironmentVariable("Path", "User")
    $python = Find-Python
}
if (-not $python) {
    throw "Python 3.11+ could not be installed automatically. Install it from https://python.org (tick 'Add python.exe to PATH') and run setup again."
}
$pythonExe = $python.Exe
$pythonArgs = @($python.Args)
Write-Host "Python: $(& $pythonExe @pythonArgs --version)"

# 2. Virtual environment ----------------------------------------------------
$venv = Join-Path $root ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    & $pythonExe @pythonArgs -m venv $venv
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
