param([string]$Mode = "enable")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "LiquidGlassSensors"

if ($Mode -eq "disable") {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        # A task that is still running cannot always be unregistered, so switch
        # it off first: a disabled task never starts at logon.
        Disable-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    exit 0
}

$exe = Join-Path $root ".venv\Scripts\pythonw.exe"
$script = Join-Path $root "main.py"

$action = New-ScheduledTaskAction -Execute $exe -Argument ('"' + $script + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -RunLevel Highest -Force | Out-Null
Write-Output "task registered"
