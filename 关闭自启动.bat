@echo off
chcp 65001 > nul
title 关闭 wechat-api 开机自启动

echo.
echo ========================================
echo  关闭 wechat-api 开机自启动
echo ========================================
echo.

echo [1/2] 禁用计划任务 SelfMediaDataServices ...
schtasks /Change /TN "SelfMediaDataServices" /Disable
if %errorlevel%==0 (
    echo   + 计划任务已禁用
) else (
    echo   ! 计划任务禁用失败（可能需要管理员权限）
)

echo.
echo [2/2] 删除启动文件夹脚本 ...
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start-all-services.vbs" (
    del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start-all-services.vbs"
    echo   + 启动文件夹脚本已删除
) else (
    echo   - 启动文件夹脚本不存在（已删除过）
)

echo.
echo ========================================
echo  完成！下次开机不会自动启动 wechat-api
echo ========================================
echo.
pause
