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

# `|| true` matters: with `set -e` plus `pipefail`, a grep that simply finds
# nothing (no .env, or no WEB_PORT line in it) would abort the whole script
# before the bot ever starts — and print nothing at all while doing it.
PORT=$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
PORT=${PORT:-8080}

echo "Starting. When it says 'Control panel', open Chrome at:"
echo "    http://localhost:${PORT}"
echo "    (if that will not load, try http://penguin.linux.test:${PORT})"
echo "Press Ctrl+C in this window to stop."
echo

exec "$PY" main.py "$@"
