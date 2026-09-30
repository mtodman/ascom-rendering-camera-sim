#!/usr/bin/env bash
# Stops the backlash focuser simulator process started by start-focusersim.sh (and, as
# a fallback, any leftover instance of this exact venv/module invocation -
# never touches unrelated Python processes).
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$PROJECT_DIR/run"
PID_FILE="$RUN_DIR/focusersim.pid"

notify() {
    # A real popup window (zenity), not a toast: stays up until clicked, or
    # auto-closes after 6s if left unattended. Backgrounded so the script
    # itself doesn't block waiting for someone to dismiss it.
    local icon="$1" message="$2" kind="--info"
    [[ "$icon" == "dialog-error" ]] && kind="--error"
    (zenity "$kind" --title="Focuser Simulator" --text="$message" --timeout=6 2>/dev/null &)
}

# A PID file surviving a reboot can point at a PID the kernel has since
# handed to a completely unrelated process. Without this check, `kill $PID`
# below would send a real signal to whatever that process now is - not just
# a missed stop, but a destructive action against something we don't own.
is_focusersim_pid() {
    local pid="$1"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    [[ -r "/proc/$pid/cmdline" ]] || return 1
    grep -qa "focuser_sim.server" "/proc/$pid/cmdline"
}

STOPPED=0

if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE")"
    if is_focusersim_pid "$PID"; then
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

# Fallback: matches "-m focuser_sim.server" regardless of whether it was
# invoked with an absolute or relative interpreter path (e.g. a shell
# started directly in $PROJECT_DIR with "./venv/bin/python ..."), since
# pkill -f matches literal argv text rather than resolved paths. This
# module name is unique to this project, so it won't touch anything else.
pkill -f "focuser_sim\.server" 2>/dev/null && STOPPED=1

if [[ "$STOPPED" -eq 1 ]]; then
    notify "media-playback-stop" "Stopped."
else
    notify "media-playback-stop" "Was not running."
fi
