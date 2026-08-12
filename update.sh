#!/usr/bin/env bash
#
# Get the latest version of the bot.
#
#   bash update.sh
#
# Pulls the newest code, installs anything new it needs, and tells you what
# changed. Your .env is never touched — it is not tracked by git.

set -euo pipefail

cd "$(dirname "$0")"

BOLD=$(printf '\033[1m'); GREEN=$(printf '\033[32m'); RESET=$(printf '\033[0m')
YELLOW=$(printf '\033[33m')   # `set -u` turns a missing one of these into a crash

BEFORE=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

echo "${BOLD}==>${RESET} Fetching the latest version..."
git pull --ff-only

AFTER=$(git rev-parse --short HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
    echo "${GREEN}  ok${RESET} Already up to date ($AFTER)."
else
    echo "${GREEN}  ok${RESET} Updated $BEFORE -> $AFTER. What changed:"
    git log --oneline "${BEFORE}..${AFTER}" | sed 's/^/       /'
fi

if [ -x .venv/bin/python ]; then
    echo "${BOLD}==>${RESET} Making sure dependencies are current..."
    .venv/bin/python -m pip install --quiet -r requirements.txt
    echo "${GREEN}  ok${RESET} dependencies fine"
fi

# Pulling new code does nothing to a bot that is already running: Python loaded
# the old files at startup and will keep using them. Refreshing the browser does
# not help either, because the page is served by that same old process. Without
# saying this out loud, "I updated and nothing changed" is the obvious and
# completely wrong conclusion.
PORT=$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
PORT=${PORT:-8080}
if command -v python3 >/dev/null 2>&1 && ! python3 - "$PORT" <<'PYEOF' 2>/dev/null
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PYEOF
then
    echo
    echo "${YELLOW}${BOLD}! The bot is still running, using the OLD code.${RESET}"
    echo "  Downloading an update does not change a program that is already going."
    echo
    echo "  Go to the terminal window running the bot, press ${BOLD}Ctrl+C${RESET},"
    echo "  then start it again with ${BOLD}bash run.sh${RESET}"
    echo
    echo "  Refreshing the web page alone will NOT pick this up."
fi

cat <<EOF

${GREEN}${BOLD}Done.${RESET} Now start it with:

    bash run.sh --paper      (practice, no account)
    bash run.sh              (your account, using .env)

One thing that catches people out: your browser caches the control panel, so
after an update press ${BOLD}Ctrl+Shift+R${RESET} on the page to force a fresh copy.
Without that you can be looking at the old panel and think nothing changed.
EOF
