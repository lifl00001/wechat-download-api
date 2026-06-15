# 开机自启配置说明

本项目提供两种方式实现 Windows 开机自动启动：

## 方式一：启动文件夹（简单推荐）

1. 按 `Win + R`，输入 `shell:startup`，回车，打开启动文件夹。
2. 将项目根目录下的 `startup.vbs` 复制到该文件夹中。
3. 重启电脑，登录后会自动在后台启动服务。
4. 查看日志：`logs\startup.log`
5. 取消自启：从启动文件夹中删除 `startup.vbs`。

## 方式二：Windows 计划任务（更稳定）

1. 右键 `setup_startup_task.bat`，选择"以管理员身份运行"。
2. 脚本会创建一个名为 `WeChatDownloadAPI_AutoStart` 的计划任务。
3. 下次登录 Windows 时自动启动服务。
4. 取消自启：运行命令

   ```cmd
   schtasks /Delete /TN WeChatDownloadAPI_AutoStart /F
   ```

## 相关文件说明

| 文件 | 作用 |
|------|------|
| `start.bat` | 交互式一键启动脚本（首次部署、手动运行时使用） |
| `start_hidden.bat` | 后台运行服务，无窗口，输出写入日志 |
| `startup.vbs` | 隐藏窗口调用 `start_hidden.bat`，用于开机自启入口 |
| `setup_startup_task.bat` | 自动创建计划任务（需管理员权限） |

## 注意事项

- 首次部署请先用 `start.bat` 完成环境配置、依赖安装和微信扫码登录。
- 开机自启前请确认 `.env` 中已有有效登录凭证，否则服务启动后仍需手动登录。
- 服务默认监听 `http://localhost:5000`。
