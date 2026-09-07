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
    notify-send -i "$1" "Camera Simulator" "$2" 2>/dev/null || true
}

mkdir -p "$RUN_DIR"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    notify "media-playback-start" "Already running (PID $(cat "$PID_FILE"))."
    exit 0
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
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    URL="$(grep -oE 'Uvicorn running on [^[:space:]]+' "$LOG_FILE" | tail -n1 | sed 's/Uvicorn running on //')"
    notify "media-playback-start" "Started (PID $(cat "$PID_FILE"))${URL:+ - $URL}."
else
    notify "dialog-error" "Failed to start - check $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi
