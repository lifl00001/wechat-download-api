' 开机自启入口脚本
' 功能：以隐藏窗口方式启动 WeChat Download API 服务
' 使用方法：
'   1. 将该文件放入 Windows 启动文件夹：按 Win+R，输入 shell:startup，回车，把本文件复制进去
'   2. 重启电脑后即可自动在后台启动服务
'   3. 日志查看：项目目录下 logs\startup.log

Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "E:\workspace\wechat-download-api\start_hidden.bat" & Chr(34), 0, False
Set WshShell = Nothing
