$t = Get-ScheduledTask -TaskName 'SelfMediaDataServices'
$i = Get-ScheduledTaskInfo -TaskName 'SelfMediaDataServices'
Write-Output "TaskName:   $($t.TaskName)"
Write-Output "State:      $($t.State)"
Write-Output "RunAsUser:  $($t.Principal.UserId)"
Write-Output "RunLevel:   $($t.Principal.RunLevel)"
Write-Output "LastRun:    $($i.LastRunTime)"
Write-Output "NextRun:    $($i.NextRunTime)"
Write-Output "LastResult: 0x$($i.LastTaskResult.ToString('X'))"
foreach ($tr in $t.Triggers) {
    Write-Output "Trigger:    $($tr.CimClass.CimClassName) user=$($tr.UserId) delay=$($tr.Delay)"
}
foreach ($ac in $t.Actions) {
    Write-Output "Action:     $($ac.Execute) $($ac.Arguments)"
}
