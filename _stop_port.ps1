# Stop process listening on a given port
param([int]$Port = 5173)
$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conns) {
    foreach ($c in $conns) {
        $procId = $c.OwningProcess
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Output "killed PID $procId on port $Port"
        } catch {
            Write-Output "failed to kill PID $procId : $($_.Exception.Message)"
        }
    }
} else {
    Write-Output "no process on port $Port"
}
