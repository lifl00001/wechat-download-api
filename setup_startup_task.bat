@echo off
chcp 65001 > nul

:: Launcher: runs the PowerShell installer with bypass policy
:: All real logic is in setup_startup_task.ps1 (which auto-elevates via UAC)

cd /d "%~dp0"

echo Launching PowerShell installer (will request UAC elevation)...
echo.
echo A UAC prompt may appear. Click "Yes" to approve.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_startup_task.ps1"

echo.
echo Installer finished. Check log: logs\setup_task.log
echo.
pause
