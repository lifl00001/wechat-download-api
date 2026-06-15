@echo off
chcp 65001 > nul

:: 后台静默启动脚本
:: 该脚本由 startup.vbs 调用，启动后不显示命令行窗口
:: 也可以单独通过 "start /min start_hidden.bat" 最小化运行

cd /d "D:\studyspace\wechat-download-api"

:: 确保日志目录存在
if not exist "logs" mkdir "logs"

:: 激活虚拟环境（如果已创建）
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [WARN] Virtual environment not found, using system Python >> logs\startup.log 2>&1
)

:: 启动服务并将输出写入日志
echo [%date% %time%] Starting WeChat Download API service... >> logs\startup.log 2>&1
python app.py >> logs\startup.log 2>&1

echo [%date% %time%] Service stopped. >> logs\startup.log 2>&1
