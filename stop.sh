#!/usr/bin/env bash
#
# Stop a bot that was started with start.sh.
#
#   bash stop.sh
#
# Asks it to shut down first (SIGTERM), which lets it close its connection to
# Pocket Option tidily, and only forces the issue if it ignores that.

set -euo pipefail

cd "$(dirname "$0")"

PIDFILE=bot.pid

if [ ! -f "$PIDFILE" ]; then
    echo "No bot.pid file, so nothing was started with start.sh."
    echo "If a panel is still loading in Chrome, it was started with run.sh —"
    echo "find that terminal window and press Ctrl+C there."
    exit 0
fi

PID=$(cat "$PIDFILE")

if ! kill -0 "$PID" 2>/dev/null; then
    echo "It is already stopped."
    rm -f "$PIDFILE"
    exit 0
fi

echo "Stopping (process $PID)..."
kill "$PID" 2>/dev/null || true

# Ten seconds is generous for a clean shutdown and short enough not to feel
# like a hang. Checked every half second so it usually returns almost at once.
for _ in $(seq 1 20); do
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PIDFILE"
        echo "Stopped. The control panel will not load until you start it again."
        echo "To start it again:  bash open_panel.sh   (or click the Bot icon)"
        exit 0
    fi
    sleep 0.5
done

echo "It did not shut down on its own — forcing it."
kill -9 "$PID" 2>/dev/null || true
rm -f "$PIDFILE"
echo "Stopped. The control panel will not load until you start it again."
echo "To start it again:  bash open_panel.sh   (or click the Bot icon)"
