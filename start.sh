#!/usr/bin/env bash
#
# Start the bot in the background, so it keeps running when you close the
# terminal window.
#
#   bash start.sh            # control panel + trading (uses your .env)
#   bash start.sh --paper    # practice mode, no account needed
#
# Why this file exists
# --------------------
# run.sh keeps the bot attached to the terminal that started it. On a Chromebook
# that is a trap: the Linux terminal is a window like any other, and closing it,
# or letting the machine sleep, takes the bot down with it. From the outside all
# you see is "the panel closed down" — the page stops loading, with nothing to
# say why and nothing left running to ask.
#
# This starts it detached instead. Closing the terminal no longer touches it,
# the output goes to bot.log rather than a window you have to keep, and
# stop.sh / status.sh replace "find the right window and press Ctrl+C".
#
# What this still cannot do: survive the Chromebook being shut down or the
# Linux container being stopped. Nothing running inside Crostini can. For
# genuine 24/7 the bot belongs on a small always-on server — see docs/SETUP.md.

set -euo pipefail

cd "$(dirname "$0")"

LOG=bot.log
PIDFILE=bot.pid

if [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    echo "Python is not installed. Run this first:" >&2
    echo "  bash install.sh" >&2
    exit 1
fi

if ! "$PY" -c "import dotenv" >/dev/null 2>&1; then
    echo "The bot's dependencies are not installed yet. Run:" >&2
    echo "  bash install.sh" >&2
    exit 1
fi

PORT=$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
PORT=${PORT:-8080}

# Already up? Say so and stop. Starting a second copy would fail on the port
# anyway, but the failure would arrive in the log file where nobody is looking.
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "The bot is already running (process $(cat "$PIDFILE"))."
    echo
    echo "  Open it:   http://localhost:${PORT}"
    echo "  Stop it:   bash stop.sh"
    echo "  Check it:  bash status.sh"
    exit 0
fi

# A stale pid file is normal — it outlives a crash or a reboot. Only the port
# tells you whether something is actually listening.
if ! "$PY" - "$PORT" <<'PYEOF' 2>/dev/null
import socket, sys
sock = socket.socket()
try:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PYEOF
then
    echo "Something is already using port ${PORT} — almost certainly the bot," >&2
    echo "started from a terminal window that is still open somewhere." >&2
    echo >&2
    echo "  Open it:   http://localhost:${PORT}" >&2
    echo "  Stop it:   bash stop.sh" >&2
    exit 1
fi

# Keep the log from growing without limit on a machine that runs for weeks.
# 5MB is thousands of lines: plenty of history, no risk of filling the disk.
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 5000000 ]; then
    mv -f "$LOG" "${LOG}.old"
fi

# setsid detaches it from this terminal's session, so closing the window does
# not send it a hangup. nohup alone is not enough on every shell.
setsid nohup "$PY" main.py "$@" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
sleep 3

if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "It started and then stopped straight away. The reason is the last few" >&2
    echo "lines here:" >&2
    echo >&2
    tail -n 20 "$LOG" >&2
    rm -f "$PIDFILE"
    exit 1
fi

echo "Running in the background. You can close this window now."
echo
echo "  ==> NEXT: open Chrome at  http://localhost:${PORT}"
echo "      (if that will not load: http://penguin.linux.test:${PORT})"
echo
# Listing these as a bare menu got 'bash stop.sh' run as though it were step
# two, which stopped the bot and made the panel look broken. They are not
# steps, so they must not be laid out like steps.
echo "Nothing else to do. Leave the bot alone unless you need one of these:"
echo "    bash status.sh    check whether it is still running"
echo "    bash stop.sh      shut it down (only when you want it OFF)"
