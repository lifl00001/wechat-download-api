$i = Get-ScheduledTaskInfo -TaskName 'SelfMediaDataServices'
Write-Output "LastRunTime: $($i.LastRunTime)"
Write-Output "LastResult: 0x$($i.LastTaskResult.ToString('X'))"
Write-Output "NextRunTime: $($i.NextRunTime)"
# 查最近系统事件日志里 newsnow / vite / node 相关错误
Write-Output ""
Write-Output "=== Recent node.exe / vite errors in Application log ==="
Get-WinEvent -FilterHashtable @{LogName='Application'; Level=2; StartTime=(Get-Date).AddHours(-2)} -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -match 'vite|newsnow|node' -or $_.ProviderName -match 'Application Error' } |
    Select-Object -First 5 TimeCreated, ProviderName, @{N='Msg';E={$_.Message.Substring(0, [Math]::Min(200, $_.Message.Length))}} |
    Format-List
