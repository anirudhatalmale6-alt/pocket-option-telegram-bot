#!/usr/bin/env bash
#
# Is the bot running, and what has it been doing?
#
#   bash status.sh
#
# The question this answers is "is it me or is it broken?" — asked whenever the
# panel will not load. It separates the three cases that look identical in
# Chrome: nothing is running, something is running but crashed on startup, and
# it is running fine and the problem is the address you typed.

set -euo pipefail

cd "$(dirname "$0")"

PIDFILE=bot.pid
LOG=bot.log

PORT=$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
PORT=${PORT:-8080}

RUNNING=no
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    RUNNING=yes
fi

if [ "$RUNNING" = yes ]; then
    echo "RUNNING  (process $(cat "$PIDFILE"))"
    echo
    echo "  Control panel:  http://localhost:${PORT}"
    echo "                  (if that will not load: http://penguin.linux.test:${PORT})"
else
    echo "NOT RUNNING"
    echo
    echo "  That is why the control panel will not load. Start it again with:"
    echo "      bash open_panel.sh"
    echo "  (or just click the Pocket Option Bot icon in your app list)"
    echo
    echo "  (If you started it with run.sh in another window, this will still"
    echo "   say NOT RUNNING — run.sh does not leave a pid file behind.)"
fi

if [ -f "$LOG" ]; then
    echo
    echo "Last few lines of the log:"
    echo "-------------------------------------------------------------"
    tail -n 15 "$LOG"
    echo "-------------------------------------------------------------"
    echo "(Full log: tail -f bot.log)"
else
    echo
    echo "No bot.log yet — it has not been started with start.sh."
fi
