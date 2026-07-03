@echo off
chcp 65001 > nul
title 自媒体数据采集服务 - 统一启动

echo.
echo ========================================
echo  自媒体数据采集服务 - 统一启动
echo  newsnow + Chrome调试 + wechat-api
echo ========================================
echo.

:: ==========================================
:: 1. 启动 newsnow 本地实例（热榜聚合）
:: ==========================================
echo [1/3] 启动 newsnow 本地实例...

:: 检查newsnow是否已在运行
netstat -ano 2>nul | findstr ":5173" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo   + newsnow 已在运行
) else (
    :: 启动newsnow（调用 newsnow 仓库里的独立启动脚本，避免重复维护）
    :: newsnow 项目在 E:\workspace\newsnow\，启动脚本在它的 scripts/startup/
    echo   ~ newsnow 启动中...
    powershell -NoProfile -ExecutionPolicy Bypass -File "E:\workspace\newsnow\scripts\startup\start-newsnow.ps1"
)

:: ==========================================
:: 2. 启动 Chrome 调试模式（CDP数据采集）
:: ==========================================
echo.
echo [2/3] 启动 Chrome 调试模式...

:: 检查Chrome调试端口是否在监听
netstat -ano 2>nul | findstr ":9222" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo   + Chrome 调试端口已在运行
) else (
    :: 启动Chrome调试模式
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="C:\ChromeDebug" "https://creator.douyin.com/creator-micro/home"
    echo   ~ Chrome 启动中，等待5秒...
    timeout /t 5 /nobreak > nul
    
    netstat -ano 2>nul | findstr ":9222" | findstr "LISTENING" >nul 2>&1
    if %errorlevel%==0 (
        echo   + Chrome 调试模式启动成功 ^| http://localhost:9222
    ) else (
        echo   ! Chrome 启动可能失败
    )
)

:: ==========================================
:: 3. 启动 wechat-download-api（数据采集API）
:: ==========================================
echo.
echo [3/3] 启动 wechat-download-api...

:: 检查5000端口是否在监听
netstat -ano 2>nul | findstr ":5000" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo   + wechat-api 已在运行
) else (
    :: 激活虚拟环境并启动
    cd /d "E:\workspace\wechat-download-api"
    if exist "venv\Scripts\activate.bat" (
        call venv\Scripts\activate.bat
    )
    start /min "wechat-api-server" cmd /c "python app.py > api-startup.log 2>&1"
    echo   ~ wechat-api 启动中，等待5秒...
    timeout /t 5 /nobreak > nul
    
    netstat -ano 2>nul | findstr ":5000" | findstr "LISTENING" >nul 2>&1
    if %errorlevel%==0 (
        echo   + wechat-api 启动成功 ^| http://localhost:5000
    ) else (
        echo   ! wechat-api 启动可能失败，请检查 api-startup.log
    )
)

:: ==========================================
:: 完成提示
:: ==========================================
echo.
echo ========================================
echo  全部服务启动完成
echo ========================================
echo.
echo 服务状态:
echo   newsnow:     http://localhost:5173  ^| 热榜聚合
echo   Chrome CDP:  http://localhost:9222  ^| 抖音数据采集
echo   wechat-api:  http://localhost:5000  ^| 数据采集API
echo.
echo 管理界面:
echo   - 新闻管理:   http://localhost:5000/news.html
echo   - 文章管理:   http://localhost:5000/articles.html
echo   - API文档:    http://localhost:5000/api/docs
echo.
echo 此窗口可以关闭，服务在后台运行。
echo ========================================
echo.

:: 如果是开机自启调用，不暂停；手动运行则暂停
if "%1"=="autostart" (
    exit /b 0
) else (
    pause
)
