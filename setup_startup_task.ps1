# ========================================================
# Register Windows Scheduled Task: boot start + auto-restart on failure
# Auto-elevates via UAC if not admin. Output also logged to setup_task.log
# ========================================================

$ErrorActionPreference = "Continue"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir     = Join-Path $ProjectDir "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$LogFile    = Join-Path $logDir "setup_task.log"
$TaskName   = "WeChatDownloadAPI_AutoStart"
$BatPath    = Join-Path $ProjectDir "start_hidden.bat"
$XmlTemplate = Join-Path $ProjectDir "wechat-autostart.xml"

function Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
}

# --- Auto-elevate via UAC if not admin ---
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($id)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Log "========================================"
Log " WeChat Download API - Task Installer"
Log "========================================"
Log "ProjectDir: $ProjectDir"
Log "IsAdmin: $isAdmin"

if (-not $isAdmin) {
    Log "Not admin. Relaunching self elevated via UAC..."
    Log "A UAC prompt will appear. Please click Yes to approve."
    $psi = New-Object System.Diagnostics.ProcessStartInfo "powershell"
    $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $psi.Verb = "runas"
    $psi.UseShellExecute = $true
    try {
        [System.Diagnostics.Process]::Start($psi) | Out-Null
    } catch {
        Log "[ERROR] UAC elevation was declined or failed: $($_.Exception.Message)"
        Log "Please right-click setup_startup_task.bat and run as administrator."
    }
    exit 0
}

try {
    Log "[OK] Running as Administrator"
    Log ""

    # Pre-checks
    if (-not (Test-Path $BatPath)) {
        Log "[ERROR] start_hidden.bat not found: $BatPath"
        Read-Host "Press Enter to exit"; exit 1
    }
    if (-not (Test-Path $XmlTemplate)) {
        Log "[ERROR] wechat-autostart.xml not found: $XmlTemplate"
        Read-Host "Press Enter to exit"; exit 1
    }
    Log "[OK] Pre-checks passed (admin + files found)"
    Log ""

    # Remove old task
    Log "[1/3] Cleaning up old task..."
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    Log "      Done"
    Log ""

    # Read template and replace path placeholders
    Log "[2/3] Generating task XML..."
    $tpl = Get-Content -Raw -Encoding UTF8 $XmlTemplate
    $tpl = $tpl.Replace("__BAT_PATH__", $BatPath)
    $tpl = $tpl.Replace("__PROJECT_DIR__", $ProjectDir)
    Log "      XML content ready"
    Log ""

    # Register the task via COM object (most robust method)
    Log "[3/3] Registering scheduled task..."
    $svc = New-Object -ComObject Schedule.Service
    $svc.Connect()
    $folder = $svc.GetFolder("\")
    # Delete if exists via COM (suppress error if not found)
    try { $folder.DeleteTask($TaskName, 0) } catch {}
    # RegisterTask: (Name, XML, Flags, UserId, Password, LogonType, SecurityDescriptor)
    # Flags = 2 (TASK_CREATE), UserId='SYSTEM' + LogonType=5 (SERVICE_ACCOUNT)
    $folder.RegisterTask($TaskName, $tpl, 2, "SYSTEM", $null, 5, $null) | Out-Null

    Log ""
    Log "========================================"
    Log " [OK] Task '$TaskName' created successfully!"
    Log "========================================"
    Log ""
    Log "Features:"
    Log "  - Boot start (BootTrigger) + Logon start (LogonTrigger)"
    Log "  - Auto-restart every 60s on crash, up to 3 times"
    Log "  - Runs as SYSTEM, hidden window"
    Log ""
    Log "Service log: $ProjectDir\logs\startup.log"
    Log "Install log: $LogFile"
    Log ""
    Log "Run it now (to verify):"
    Log "  schtasks /Run /TN $TaskName"
    Log ""
    Log "To remove auto-start:"
    Log "  schtasks /Delete /TN $TaskName /F"
}
catch {
    Log ""
    Log "========================================"
    Log " [ERROR] $($_.Exception.Message)"
    Log "========================================"
    Log $_.ScriptStackTrace
}
finally {
    Log ""
    Log "Full log saved to: $LogFile"
    Write-Host ""
    Write-Host "Press Enter to close..." -NoNewline
    Read-Host
}
