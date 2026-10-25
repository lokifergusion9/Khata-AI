# Registers the 3 Task Scheduler tasks for this project under "\MCP Finance\" (current user).
# Safe to run again: -Force replaces existing tasks.
#   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$folder = "\MCP Finance\"
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

function New-HiddenAction($script) {
    # pythonw + run_hidden.pyw: no console window, and the script's exit code reaches Task Scheduler
    # (conhost --headless hides the window too, but always reports 0; S4U tasks need admin)
    New-ScheduledTaskAction -Execute "$root\.venv\Scripts\pythonw.exe" `
        -Argument "`"$root\scripts\run_hidden.pyw`" `"$root\scripts\$script`"" -WorkingDirectory $root
}

# 1. WhatsApp bridge: start at logon, keep running, restart if it crashes
$bridgeSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskPath $folder -TaskName "WhatsApp Bridge" -Force `
    -Description "Starts the whatsapp-mcp bridge at logon. Log: data\logs\bridge.log" `
    -Action (New-HiddenAction "run_bridge.cmd") `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
    -Settings $bridgeSettings -Principal $principal | Out-Null

# 2. Ingestion: every 30 minutes; a missed run (laptop asleep) runs as soon as possible
$next = (Get-Date).Date.AddMinutes([math]::Ceiling((Get-Date).TimeOfDay.TotalMinutes / 30) * 30)
$ingestTrigger = New-ScheduledTaskTrigger -Once -At $next -RepetitionInterval (New-TimeSpan -Minutes 30)
$ingestSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable
Register-ScheduledTask -TaskPath $folder -TaskName "Ingestion" -Force `
    -Description "Runs client.py every 30 min (WhatsApp -> Sheet). Log: data\logs\client.log" `
    -Action (New-HiddenAction "run_client.cmd") `
    -Trigger $ingestTrigger -Settings $ingestSettings -Principal $principal | Out-Null

# 3. Daily summary at 21:00. No StartWhenAvailable: a late run would summarise the wrong day
$summarySettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskPath $folder -TaskName "Daily Summary" -Force `
    -Description "Runs summary.py daily at 21:00 (ledger totals -> WhatsApp). Log: data\logs\summary.log" `
    -Action (New-HiddenAction "run_summary.cmd") `
    -Trigger (New-ScheduledTaskTrigger -Daily -At "21:00") `
    -Settings $summarySettings -Principal $principal | Out-Null

Get-ScheduledTask -TaskPath $folder | ForEach-Object {
    $info = $_ | Get-ScheduledTaskInfo
    "{0,-16} {1,-6} next run: {2}" -f $_.TaskName, $_.State, $info.NextRunTime
}
