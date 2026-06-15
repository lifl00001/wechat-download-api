@echo off
chcp 65001 > nul

:: ========================================================
:: 安装为 Windows 计划任务，实现开机自动启动
:: 请以"管理员身份"运行此脚本
:: ========================================================

cd /d "D:\studyspace\wechat-download-api"

set "TASK_NAME=WeChatDownloadAPI_AutoStart"
set "PROJECT_DIR=D:\studyspace\wechat-download-api"
set "VBS_PATH=%PROJECT_DIR%\startup.vbs"

:: 删除旧任务（如果存在）
schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>&1

:: 创建新任务：用户登录时自动启动，使用最高权限，隐藏窗口
schtasks /Create ^
    /TN "%TASK_NAME%" ^
    /TR "wscript.exe \"%VBS_PATH%\"" ^
    /SC ONLOGON ^
    /RL HIGHEST ^
    /F

if errorlevel 1 (
    echo.
    echo [ERROR] 创建计划任务失败，请确认是否以管理员身份运行。
    pause
    exit /b 1
)

echo.
echo [OK] 计划任务 "%TASK_NAME%" 创建成功！
echo 下次登录 Windows 时将自动在后台启动 WeChat Download API 服务。
echo 服务日志：%PROJECT_DIR%\logs\startup.log
echo.
echo 如需取消开机启动，运行以下命令：
echo   schtasks /Delete /TN "%TASK_NAME%" /F
pause
