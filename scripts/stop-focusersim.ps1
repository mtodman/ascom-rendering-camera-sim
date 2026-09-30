# Stops the focuser simulator started by start-focusersim.ps1 (and, as a
# fallback, any other `python -m focuser_sim.server` process - never touches
# unrelated Python processes). Windows counterpart of stop-focusersim.sh.
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectDir 'run\focusersim.pid'

function Notify([string]$Message) {
    (New-Object -ComObject WScript.Shell).Popup($Message, 6, 'Focuser Simulator', 64) | Out-Null
}

# Matching on the module name (unique to this project) covers both the PID
# file's process and any instance started by hand from a terminal.
$procs = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*focuser_sim.server*' })

foreach ($p in $procs) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue

if ($procs.Count -gt 0) { Notify 'Stopped.' } else { Notify 'Was not running.' }
