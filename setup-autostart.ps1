# Register SelfMediaDataServices auto-start task at logon
# Usage: Right-click -> Run with PowerShell
# Idempotent: removes old task first, then creates new one
# All output also goes to setup-autostart-error.log so you can inspect after crash

$ErrorActionPreference = "Continue"
$logFile = "E:\workspace\wechat-download-api\setup-autostart-error.log"
"=== Run at $(Get-Date) ===" | Out-File $logFile -Encoding UTF8

function Log($msg) {
    Write-Host $msg
    $msg | Out-File $logFile -Append -Encoding UTF8
}

try {
    # Check admin privilege
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    Log "IsAdmin=$isAdmin"

    if (-not $isAdmin) {
        Log "NOT ADMIN. A UAC dialog will appear - click YES to continue."
        Start-Sleep -Seconds 2
        $script = $MyInvocation.MyCommand.Path
        Log "Spawning elevated process with -Wait (this window stays open)..."
        # -Wait keeps THIS window open until child finishes, so no flash-close
        Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
        Log "Elevated process finished."
        Start-Sleep -Seconds 3
        exit 0
    }

    # ---- BELOW RUNS AS ADMIN ----
    Log "Running as Administrator"
    $taskName = "SelfMediaDataServices"
    $batPath = "E:\workspace\wechat-download-api\start-all.bat"
    Log "TaskName=$taskName BatPath=$batPath"

    if (-not (Test-Path $batPath)) {
        throw "BAT not found: $batPath"
    }
    Log "OK: start-all.bat exists"

    Log "Removing old task if exists..."
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Log "Old task removed"
    } else {
        Log "No old task"
    }

    Log "Building task definition..."
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "Administrator"
    $trigger.Delay = "PT30S"
    $action = New-ScheduledTaskAction -Execute $batPath -Argument "autostart"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    $principal = New-ScheduledTaskPrincipal -UserId "Administrator" -RunLevel Highest

    Log "Registering task..."
    Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $action -Settings $settings -Principal $principal -Force | Out-Null
    Log "OK: Task registered"

    Log "Verifying..."
    $task = Get-ScheduledTask -TaskName $taskName
    Log ("TaskName=" + $task.TaskName + " State=" + $task.State + " User=" + $task.Principal.UserId + " Level=" + $task.Principal.RunLevel)

    Log ""
    Log "========================================"
    Log "  SUCCESS: Auto-start configured!"
    Log "========================================"
    Log "  Next logon auto-starts:"
    Log "    1. newsnow (localhost:5173)"
    Log "    2. Chrome CDP (localhost:9222)"
    Log "    3. wechat-api (localhost:5000)"
    Log ""
    Log "DONE. Window closes in 10s."
    Start-Sleep -Seconds 10

} catch {
    Log ("ERROR: " + $_.Exception.Message)
    Log $_.ScriptStackTrace
    Log ""
    Log "FAILED. Window closes in 15s. See $logFile"
    Start-Sleep -Seconds 15
    exit 1
}
