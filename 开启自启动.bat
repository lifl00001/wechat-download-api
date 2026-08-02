@echo off
chcp 65001 > nul
title 开启 wechat-api 开机自启动

echo.
echo ========================================
echo  开启 wechat-api 开机自启动
echo ========================================
echo.

echo [1/2] 启用计划任务 SelfMediaDataServices ...
schtasks /Change /TN "SelfMediaDataServices" /Enable
if %errorlevel%==0 (
    echo   + 计划任务已启用
) else (
    echo   ! 计划任务启用失败（可能需要管理员权限）
)

echo.
echo [2/2] 恢复启动文件夹脚本 ...
copy /Y "E:\workspace\wechat-download-api\scripts\startup\start-all-services.vbs" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start-all-services.vbs" > nul 2>&1
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start-all-services.vbs" (
    echo   + 启动文件夹脚本已恢复
) else (
    echo   ! 启动文件夹脚本恢复失败
)

echo.
echo ========================================
echo  完成！下次开机将自动启动 wechat-api
echo ========================================
echo.
echo  验证命令：
echo    schtasks /Query /TN "SelfMediaDataServices" /FO CSV
echo    （看到"就绪"表示已开启）
echo.
pause
