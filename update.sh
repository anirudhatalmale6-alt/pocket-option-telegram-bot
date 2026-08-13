#!/usr/bin/env bash
#
# Get the latest version of the bot.
#
#   bash update.sh
#
# Pulls the newest code, installs anything new it needs, and tells you what
# changed. Your .env is never touched — it is not tracked by git.

set -euo pipefail

# Everything below lives in a function that is only called on the very last
# line. This script git-pulls a new copy of ITSELF while bash is still reading
# it, and bash reads a script incrementally, by byte offset — so when the file
# changes length mid-run, execution can resume in the middle of a line and die
# with nonsense like "syntax error near unexpected token". Wrapping the body
# forces bash to parse all of it up front, before the pull can move anything.
main() {

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
RESTARTED=no

# Come back up in the same mode it was in. Silently promoting a practice run to
# a real-money one during an update would be unforgivable, so --paper is the
# default and only a saved session removes it.
START_ARGS=--paper
if grep -qE '^PO_(SESSION|SSID)=.+' .env 2>/dev/null; then
    START_ARGS=
fi

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
    # Do it, don't ask. Downloading an update never changes a program already
    # running, and every time that has been left to a follow-up instruction it
    # has cost a round trip: the fix is downloaded, the old code is still what
    # answers, and the bug looks unfixed. There is nothing to decide here.
    echo
    echo "${BOLD}==>${RESET} The bot is running the old code — restarting it..."
    if bash stop.sh >/dev/null 2>&1 && bash start.sh ${START_ARGS} >/dev/null 2>&1; then
        echo "${GREEN}  ok${RESET} restarted, now running ${AFTER}"
        RESTARTED=yes
    else
        echo "${YELLOW}  ! could not restart it automatically.${RESET}"
        echo "    Run this yourself:  ${BOLD}bash stop.sh && bash open_panel.sh${RESET}"
    fi
fi

# Keep the launcher icon in step with the code, silently. Asking someone to run
# a second command to get the thing whose whole purpose is to save them running
# commands would be a strange way round.
if [ -f install_launcher.sh ]; then
    bash install_launcher.sh >/dev/null 2>&1 || true
fi

if [ "$RESTARTED" = yes ]; then
cat <<EOF

${GREEN}${BOLD}Done — and the bot is already running the new code.${RESET}

Just go to your control panel tab and press ${BOLD}Ctrl+Shift+R${RESET}.

    http://localhost:${PORT}

That last step matters: your browser keeps a copy of the page, so without a
hard refresh you can be looking at the old one and think nothing changed.
EOF
else
cat <<EOF

${GREEN}${BOLD}Done.${RESET} Now do this one thing:

    ${BOLD}bash open_panel.sh${RESET}

That starts the bot and opens the control panel by itself. Nothing else is
needed, and nothing has to stay open afterwards.

There is now also a ${BOLD}Pocket Option Bot${RESET} icon in your Chromebook's app list
(under "Linux apps") that does exactly the same thing. Drag it to your shelf
and you never need this terminal again.

One thing that catches people out: your browser caches the control panel, so
after an update press ${BOLD}Ctrl+Shift+R${RESET} on the page to force a fresh copy.
Without that you can be looking at the old panel and think nothing changed.
EOF
fi

}

main "$@"
