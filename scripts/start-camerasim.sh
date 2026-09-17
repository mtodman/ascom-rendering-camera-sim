#!/usr/bin/env bash
# Starts the ASCOM Alpaca sky-rendering camera simulator, detached from
# whatever launched this script (desktop icon, terminal, etc).
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/venv/bin/python"
RUN_DIR="$PROJECT_DIR/run"
PID_FILE="$RUN_DIR/camerasim.pid"
LOG_FILE="$RUN_DIR/camerasim.log"

notify() {
    # A real popup window (zenity), not a toast: stays up until clicked, or
    # auto-closes after 6s if left unattended. Backgrounded so the script
    # itself doesn't block waiting for someone to dismiss it.
    local icon="$1" message="$2" kind="--info"
    [[ "$icon" == "dialog-error" ]] && kind="--error"
    (zenity "$kind" --title="Camera Simulator" --text="$message" --timeout=6 2>/dev/null &)
}

# A PID file surviving a reboot can point at a PID that the kernel has since
# handed to a completely unrelated process, so `kill -0` alone isn't enough -
# it happily reports "alive" for that impostor. Cross-check /proc's cmdline
# too, since that's reset (and PIDs reassigned) on every boot.
is_camerasim_pid() {
    local pid="$1"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    [[ -r "/proc/$pid/cmdline" ]] || return 1
    grep -qa "camera_sim.server" "/proc/$pid/cmdline"
}

mkdir -p "$RUN_DIR"

if [[ -f "$PID_FILE" ]] && is_camerasim_pid "$(cat "$PID_FILE")"; then
    notify "media-playback-start" "Already running (PID $(cat "$PID_FILE"))."
    exit 0
elif [[ -f "$PID_FILE" ]]; then
    # Stale PID file (process gone, or reused by something else since the
    # last boot) - clear it out so it can't be mistaken for a live run again.
    rm -f "$PID_FILE"
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    notify "dialog-error" "venv not found at $PYTHON_BIN - run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

: >"$LOG_FILE"
(
    cd "$PROJECT_DIR" || exit 1
    exec "$PYTHON_BIN" -m camera_sim.server >"$LOG_FILE" 2>&1
) &
disown
echo $! >"$PID_FILE"

sleep 2
if is_camerasim_pid "$(cat "$PID_FILE")"; then
    URL="$(grep -oE 'Uvicorn running on [^[:space:]]+' "$LOG_FILE" | tail -n1 | sed 's/Uvicorn running on //')"
    notify "media-playback-start" "Started (PID $(cat "$PID_FILE"))${URL:+ - $URL}."
else
    notify "dialog-error" "Failed to start - check $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi
