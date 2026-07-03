@echo off
chcp 65001 > nul
:: ==========================================
:: 配置开机自动启动所有数据采集服务
:: 使用Windows任务计划程序，登录后延迟30秒启动
::
:: 必须右键"以管理员身份运行"！
:: 幂等：可重复运行，会先删旧任务再建新的
:: ==========================================

echo.
echo ========================================
echo  配置开机自动启动（需管理员权限）
echo ========================================
echo.

:: 1. 检查管理员权限
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo   ❌ 没有管理员权限！
    echo.
    echo   请右键此文件 → "以管理员身份运行"
    echo   （或在开始菜单搜索"cmd"→ 右键"以管理员身份运行"→ cd 到本目录→ 执行本脚本）
    echo.
    pause
    exit /b 1
)
echo   ✅ 已确认管理员权限

:: 2. 检查 start-all.bat 是否存在
if not exist "%~dp0start-all.bat" (
    echo   ❌ 找不到 %~dp0start-all.bat
    pause
    exit /b 1
)
echo   ✅ start-all.bat 存在

:: 3. 删除旧任务（幂等，忽略"不存在"错误）
echo.
echo [1/3] 清理旧任务...
schtasks /query /tn "SelfMediaDataServices" >nul 2>&1
if %errorlevel%==0 (
    schtasks /delete /tn "SelfMediaDataServices" /f >nul 2>&1
    echo   - 已删除旧任务
) else (
    echo   - 无旧任务（首次配置）
)

:: 4. 创建新任务
::    /sc onlogon    登录时触发
::    /ru "%USERNAME%"  指定当前用户（避免多用户环境混乱）
::    /delay 0000:30 延迟30秒（等网络就绪）
::    /rl highest    以最高权限运行
echo.
echo [2/3] 创建开机自启任务...
set "BAT_PATH=%~dp0start-all.bat"
schtasks /create /tn "SelfMediaDataServices" /tr "\"%BAT_PATH%\" autostart" /sc onlogon /ru "%USERNAME%" /delay 0000:30 /rl highest /f

if %errorlevel% neq 0 (
    echo.
    echo   ❌ 任务创建失败（错误码 %errorlevel%）
    echo   常见原因：
    echo     - 密码策略问题：试试加 /it 参数（交互式，不存密码）
    echo     - 用户名含特殊字符：手动执行 schtasks 命令
    echo.
    pause
    exit /b 1
)

:: 5. 验证任务已注册
echo.
echo [3/3] 验证任务注册...
schtasks /query /tn "SelfMediaDataServices" /fo list 2>&1 | findstr /i "TaskName Status Run As User Schedule Task To Run"

echo.
echo ========================================
echo   ✅ 开机自启配置成功！
echo ========================================
echo.
echo   任务名:   SelfMediaDataServices
echo   触发:     每次登录后延迟30秒
echo   启动:     start-all.bat autostart
echo   包含:
echo     1. newsnow 热榜聚合 ^(localhost:5173^)
echo     2. Chrome 调试模式 ^(localhost:9222^)
echo     3. wechat-api 数据采集 ^(localhost:5000^)
echo.
echo   管理命令:
echo     查看状态: schtasks /query /tn "SelfMediaDataServices" /v
echo     立即运行: schtasks /run /tn "SelfMediaDataServices"
echo     删除任务: schtasks /delete /tn "SelfMediaDataServices" /f
echo.
echo   提示: 现在可以测试一下 —— 执行上面的"立即运行"命令，
echo         或注销重新登录，验证服务自动起来。
echo.

:: 询问是否立即测试
set /p testnow="是否现在立即运行一次测试？(y/n): "
if /i "%testnow%"=="y" (
    echo.
    echo 正在运行任务...
    schtasks /run /tn "SelfMediaDataServices"
    echo 任务已触发，请在30秒后检查：
    echo   http://localhost:5173  ^(newsnow^)
    echo   http://localhost:5000  ^(wechat-api^)
)

pause
