#!/usr/bin/env bash
# Stops the camera simulator process started by start-camerasim.sh (and, as
# a fallback, any leftover instance of this exact venv/module invocation -
# never touches unrelated Python processes).
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$PROJECT_DIR/run"
PID_FILE="$RUN_DIR/camerasim.pid"

notify() {
    notify-send -i "$1" "Camera Simulator" "$2" 2>/dev/null || true
}

# A PID file surviving a reboot can point at a PID the kernel has since
# handed to a completely unrelated process. Without this check, `kill $PID`
# below would send a real signal to whatever that process now is - not just
# a missed stop, but a destructive action against something we don't own.
is_camerasim_pid() {
    local pid="$1"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    [[ -r "/proc/$pid/cmdline" ]] || return 1
    grep -qa "camera_sim.server" "/proc/$pid/cmdline"
}

STOPPED=0

if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE")"
    if is_camerasim_pid "$PID"; then
        kill "$PID" 2>/dev/null
        for _ in $(seq 1 20); do
            kill -0 "$PID" 2>/dev/null || break
            sleep 0.2
        done
        kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
        STOPPED=1
    fi
    rm -f "$PID_FILE"
fi

# Fallback: matches "-m camera_sim.server" regardless of whether it was
# invoked with an absolute or relative interpreter path (e.g. a shell
# started directly in $PROJECT_DIR with "./venv/bin/python ..."), since
# pkill -f matches literal argv text rather than resolved paths. This
# module name is unique to this project, so it won't touch anything else.
pkill -f "camera_sim\.server" 2>/dev/null && STOPPED=1

if [[ "$STOPPED" -eq 1 ]]; then
    notify "media-playback-stop" "Stopped."
else
    notify "media-playback-stop" "Was not running."
fi
