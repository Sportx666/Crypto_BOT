# Runs the Hyperliquid bot (bot/main.py) with auto-restart on crash.
# Mirrors the `restart: unless-stopped` behavior the Docker deployment will
# have on the VPS, so this local 48h trial is a realistic dry run of that.
#
# Usage:
#   Open a PowerShell window in the repo root and run:
#     .\run_hl_bot_watchdog.ps1
#   Leave the window open for the duration of the trial.
#
# Stop it with Ctrl+C, or from another window:
#     New-Item -ItemType File -Path .\STOP_BOT -Force

$ErrorActionPreference = "Continue"
$stopFile = Join-Path $PSScriptRoot "STOP_BOT"
$restartDelaySeconds = 10

if (Test-Path $stopFile) {
    Remove-Item $stopFile -Force
}

Write-Host "=== HL bot watchdog starting. Ctrl+C to stop, or create STOP_BOT to stop after current run. ==="

while ($true) {
    if (Test-Path $stopFile) {
        Write-Host "$(Get-Date -Format o)  STOP_BOT found - exiting watchdog."
        Remove-Item $stopFile -Force
        break
    }

    Write-Host "$(Get-Date -Format o)  Starting bot.main..."
    python -m bot.main
    $exitCode = $LASTEXITCODE

    if (Test-Path $stopFile) {
        Write-Host "$(Get-Date -Format o)  bot.main exited (code $exitCode). STOP_BOT present - not restarting."
        Remove-Item $stopFile -Force
        break
    }

    Write-Host "$(Get-Date -Format o)  bot.main exited (code $exitCode). Restarting in $restartDelaySeconds s..."
    Start-Sleep -Seconds $restartDelaySeconds
}
