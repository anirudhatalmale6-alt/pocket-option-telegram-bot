#!/usr/bin/env bash
#
# Start the bot if it is not already running, then open the control panel.
#
#   bash open_panel.sh
#
# This is what the "Pocket Option Bot" icon in the Chromebook launcher runs, so
# it has to be safe to click at any time: when nothing is running it starts the
# bot, when something is already running it leaves it completely alone and just
# opens the page. Clicking it twice must never start two bots or kill a live
# one — a second copy would fight the first over the same account.
#
# Why an icon at all: every failure on this project so far has come from the
# terminal, not the bot. Windows closed, commands mistyped, a listed
# "bash stop.sh" run as if it were the next step. Removing the terminal from
# normal use removes that whole class of problem.

set -euo pipefail

cd "$(dirname "$0")"

PORT=$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
PORT=${PORT:-8080}
URL="http://localhost:${PORT}"

if [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
else
    PY=python3
fi

listening() {
    ! "$PY" - "$PORT" <<'PYEOF' 2>/dev/null
import socket, sys
sock = socket.socket()
try:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PYEOF
}

# --paper unless a real session has been saved. Starting in live mode with no
# account configured would only produce a wall of connection errors.
MODE=--paper
if grep -qE '^PO_(SESSION|SSID)=.+' .env 2>/dev/null; then
    MODE=
fi

if listening; then
    echo "Already running."
else
    echo "Starting the bot..."
    # start.sh's own output ends with "open Chrome at ...", which contradicts
    # this script — it opens Chrome itself. Keep it back unless it failed, in
    # which case every line of it matters.
    if ! OUT=$(bash start.sh ${MODE} 2>&1); then
        echo "$OUT"
        echo
        echo "It would not start. The reason is above."
        echo "Press Enter to close this window."
        read -r _
        exit 1
    fi

    # start.sh returns as soon as the process is alive, which is a moment before
    # the web server has bound its port. Opening the browser in that gap shows
    # "site cannot be reached" and looks exactly like a failure.
    for _ in $(seq 1 20); do
        listening && break
        sleep 0.5
    done
fi

echo "Opening ${URL}"

# In Crostini, xdg-open hands the URL to ChromeOS, so the panel opens in the
# normal Chrome the client already uses rather than inside the container.
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 &
elif command -v garcon-url-handler >/dev/null 2>&1; then
    garcon-url-handler "$URL" >/dev/null 2>&1 &
else
    echo "Could not open Chrome automatically — type this in yourself:"
    echo "    $URL"
fi
