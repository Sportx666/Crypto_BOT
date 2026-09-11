# Quick at-a-glance health check for the headless HL bot (bot/main.py).
# Usage: .\check_hl_bot_status.ps1

$repoRoot = $PSScriptRoot
$statePath = Join-Path $repoRoot "data\state.json"
$logPath = Join-Path $repoRoot "data\bot.log"

Write-Host "=== HL bot status ===" -ForegroundColor Cyan

# 1. Is a bot.main process actually running?
# WMI sometimes returns a blank CommandLine for recently-spawned python.exe
# processes, so a CommandLine match alone can false-negative. Treat any
# python.exe whose command line is unreadable as "possibly ours" too, and
# corroborate with the freshness of state.json below.
$allPython = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue
$proc = $allPython | Where-Object { $_.CommandLine -match "bot\.main" }
$unknownCmdline = $allPython | Where-Object { [string]::IsNullOrEmpty($_.CommandLine) }

if ($proc) {
    foreach ($p in $proc) {
        Write-Host "Process:   RUNNING (PID $($p.ProcessId), started $($p.CreationDate))" -ForegroundColor Green
    }
} elseif ($unknownCmdline) {
    Write-Host "Process:   UNKNOWN - python.exe present but command line unreadable (PIDs: $($unknownCmdline.ProcessId -join ', ')). Check 'Last save' below instead." -ForegroundColor Yellow
} else {
    Write-Host "Process:   NOT RUNNING" -ForegroundColor Red
}

# 2. How fresh is the last log line / state save?
if (Test-Path $statePath) {
    $state = Get-Content $statePath -Raw | ConvertFrom-Json
    $savedAt = [DateTimeOffset]::FromUnixTimeSeconds([int64]$state.saved_at).LocalDateTime
    $ageMin = [math]::Round(((Get-Date) - $savedAt).TotalMinutes, 1)

    $ageColor = if ($ageMin -lt 3) { "Green" } elseif ($ageMin -lt 10) { "Yellow" } else { "Red" }
    Write-Host "Last save: $savedAt ($ageMin min ago)" -ForegroundColor $ageColor

    $halted = $state.risk.halted
    $haltColor = if ($halted) { "Red" } else { "Green" }
    Write-Host "Halted:    $halted $(if ($halted) { '- ' + $state.risk.halt_reason })" -ForegroundColor $haltColor

    Write-Host "Daily P&L:  $($state.risk.loss_tracker.daily_pnl)"
    Write-Host "Weekly P&L: $($state.risk.loss_tracker.weekly_pnl)"

    $posCount = ($state.risk.positions.PSObject.Properties | Measure-Object).Count
    Write-Host "Open positions: $posCount"
    foreach ($pos in $state.risk.positions.PSObject.Properties) {
        $p = $pos.Value
        Write-Host "  - $($pos.Name): dir=$($p.direction) entry=$($p.entry_price) size=$($p.size)"
    }
} else {
    Write-Host "Last save: no state.json yet (bot hasn't completed a save cycle)" -ForegroundColor Yellow
}

# 3. Last few log lines
if (Test-Path $logPath) {
    Write-Host "`n--- Last 8 log lines ---" -ForegroundColor Cyan
    Get-Content $logPath -Tail 8
} else {
    Write-Host "`nNo log file at $logPath yet." -ForegroundColor Yellow
}
