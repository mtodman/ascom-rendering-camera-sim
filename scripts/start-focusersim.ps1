# Starts the ASCOM Alpaca backlash focuser simulator on Windows, detached and
# hidden (no console window), e.g. from a desktop shortcut. Windows
# counterpart of start-focusersim.sh.
$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonBin = Join-Path $ProjectDir 'venv\Scripts\python.exe'
$RunDir = Join-Path $ProjectDir 'run'
$PidFile = Join-Path $RunDir 'focusersim.pid'
$LogFile = Join-Path $RunDir 'focusersim.log'
$ErrLogFile = Join-Path $RunDir 'focusersim.err.log'

function Notify([string]$Message, [bool]$IsError = $false) {
    # A real popup (not a toast): stays up until clicked, or auto-closes
    # after 6s if left unattended. 16 = error icon, 64 = info icon.
    $icon = if ($IsError) { 16 } else { 64 }
    (New-Object -ComObject WScript.Shell).Popup($Message, 6, 'Focuser Simulator', $icon) | Out-Null
}

# A stale PID file (e.g. after a reboot) can point at a PID Windows has since
# handed to an unrelated process - check the command line, not just the PID.
function Test-FocuserSimPid([string]$ProcessId) {
    if (-not $ProcessId) { return $false }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    return [bool]($proc -and $proc.CommandLine -like '*focuser_sim.server*')
}

New-Item -ItemType Directory -Force $RunDir | Out-Null

if (Test-Path $PidFile) {
    $existing = (Get-Content $PidFile -Raw).Trim()
    if (Test-FocuserSimPid $existing) {
        Notify "Already running (PID $existing)."
        exit 0
    }
    Remove-Item $PidFile -Force
}

if (-not (Test-Path $PythonBin)) {
    Notify "venv not found at $PythonBin - run: python -m venv venv; venv\Scripts\pip install -r requirements.txt" $true
    exit 1
}

$proc = Start-Process -FilePath $PythonBin -ArgumentList '-m', 'focuser_sim.server' `
    -WorkingDirectory $ProjectDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $LogFile -RedirectStandardError $ErrLogFile
Set-Content -Path $PidFile -Value $proc.Id

Start-Sleep -Seconds 3
if (Test-FocuserSimPid $proc.Id) {
    # uvicorn logs to stderr.
    $url = Select-String -Path $ErrLogFile -Pattern 'Uvicorn running on (\S+)' |
        Select-Object -Last 1 | ForEach-Object { $_.Matches[0].Groups[1].Value }
    $page = if ($url) { " - setup page: $($url -replace '0\.0\.0\.0', 'localhost')/setup" } else { '' }
    Notify "Started (PID $($proc.Id))$page."
} else {
    Notify "Failed to start - check $ErrLogFile" $true
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    exit 1
}
