#!/usr/bin/env bash
#
# Start the bot.
#
#   bash run.sh            # control panel + trading (uses your .env)
#   bash run.sh --paper    # practice mode, no account needed
#
# Why this file exists: on Debian — which is what a Chromebook's Linux is —
# there is no `python` command, only `python3`. Typing `python main.py` gives
# "bash: python: command not found", which reads like the bot is broken when
# nothing is wrong at all. And even `python3 main.py` is not right here,
# because the dependencies live in the .venv folder, not system-wide.
#
# So: one command that works, and finds the right interpreter itself.

set -euo pipefail

cd "$(dirname "$0")"

if [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
    echo "! No .venv found — using system python3."
    echo "  If the next line is an import error, run:  bash install.sh"
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Python is not installed. Run this first:" >&2
    echo "  bash install.sh" >&2
    exit 1
fi

# Fail with an instruction rather than a stack trace when deps are missing.
if ! "$PY" -c "import dotenv" >/dev/null 2>&1; then
    echo "The bot's dependencies are not installed yet. Run:" >&2
    echo "  bash install.sh" >&2
    exit 1
fi

# Say so when this copy is out of date. Nearly every "the thing you described
# isn't on my screen" turns out to be old code, and there was previously no way
# to tell from the panel — you just saw a feature missing and assumed it was
# broken. Never blocks startup: short timeout, and silence on any failure,
# because being offline must not stop the bot from running.
if command -v git >/dev/null 2>&1 && [ -d .git ]; then
    if timeout 6 git fetch --quiet 2>/dev/null; then
        BEHIND=$(git rev-list --count HEAD..@{u} 2>/dev/null || echo 0)
        if [ "${BEHIND:-0}" -gt 0 ] 2>/dev/null; then
            echo "! Your copy is ${BEHIND} update(s) behind."
            echo "  Press Ctrl+C and run:  bash update.sh"
            echo "  Carrying on with the version you have..."
            echo
        fi
    fi
fi

# `|| true` matters: with `set -e` plus `pipefail`, a grep that simply finds
# nothing (no .env, or no WEB_PORT line in it) would abort the whole script
# before the bot ever starts — and print nothing at all while doing it.
PORT=$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
PORT=${PORT:-8080}

# "Address already in use" is a stack trace that means something reassuring:
# the bot is ALREADY RUNNING, in a terminal window you forgot about. Said in
# Python's words it reads like a crash, so say it in plain ones instead.
#
# Deliberately does NOT kill the other process. That copy may have a trade open
# on a real account, and silently killing it to free a port would be a far worse
# outcome than being told to go and close it yourself.
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
    echo "The bot looks like it is already running." >&2
    echo >&2
    echo "  To use the one that is already going:" >&2
    echo "      open  http://localhost:${PORT}  in Chrome" >&2
    echo >&2
    echo "  To restart it instead:" >&2
    echo "      find the other terminal window, press Ctrl+C there," >&2
    echo "      then run this again." >&2
    echo >&2
    echo "(Nothing is broken, and you do not need to log out of anything.)" >&2
    exit 1
fi

echo "Starting. When it says 'Control panel', open Chrome at:"
echo "    http://localhost:${PORT}"
echo "    (if that will not load, try http://penguin.linux.test:${PORT})"
echo "Press Ctrl+C in this window to stop."
echo

exec "$PY" main.py "$@"
