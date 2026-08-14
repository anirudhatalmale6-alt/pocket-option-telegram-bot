#!/usr/bin/env bash
#
# Get the latest version of the bot.
#
#   bash update.sh
#
# Pulls the newest code, installs anything new it needs, restarts the bot on it
# and opens the control panel. Your .env is never touched — it is not tracked
# by git.

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

PORT=$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
PORT=${PORT:-8080}

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

# Pulling new code does nothing to a bot that is already running: Python loaded
# the old files at startup and will keep using them. Refreshing the browser does
# not help either, because the page is served by that same old process. So stop
# it — and then start it again unconditionally, below.
if listening; then
    echo "${BOLD}==>${RESET} Stopping the old version..."
    # Deliberately not `stop.sh && start.sh`. Chaining them on && means any
    # non-zero exit from the stop leaves the bot down and never starts it back
    # up, which is the worst possible outcome for a script whose entire job is
    # to leave you running the new code.
    bash stop.sh >/dev/null 2>&1 || true
fi

# ALWAYS end with the bot running and the panel open.
#
# This used to restart only when it found something already listening. Run it
# while the bot happened to be stopped and it printed a green "Done." and left
# nothing running at all — so localhost would not load, which looks exactly
# like an update that has broken everything. It was reported as "after every cd
# you send it wont connect to local host", after four rounds of it, and that
# was a fair description of what this script actually did.
#
# open_panel.sh is idempotent and already knows how to pick practice vs a real
# session, so it is the one place that decides how the bot starts. Duplicating
# that choice here is how the two would drift apart, and one of the two answers
# is somebody's real money.
echo "${BOLD}==>${RESET} Starting it on the new code..."
if bash open_panel.sh; then
    cat <<EOF

${GREEN}${BOLD}Done — the bot is running the new code and the panel is opening.${RESET}

If the page was already open in another tab, press ${BOLD}Ctrl+Shift+R${RESET} on it.
Your browser keeps a copy, so without that you can be looking at the old panel
and think nothing changed.

    http://localhost:${PORT}
EOF
else
    cat <<EOF

${YELLOW}${BOLD}The update downloaded, but the bot would not start.${RESET}

The reason is in the lines above this. Send me a photo of them and I will
tell you exactly what it is.
EOF
fi

# Keep the launcher icon in step with the code, silently. Asking someone to run
# a second command to get the thing whose whole purpose is to save them running
# commands would be a strange way round.
if [ -f install_launcher.sh ]; then
    bash install_launcher.sh >/dev/null 2>&1 || true
fi

# Same reasoning for the autostart registration. "The localhost website isn't
# working" has been reported three times now, and every time the cause was the
# Linux container having been stopped — by a shutdown, or by ChromeOS deciding
# to stop it — taking the bot with it. Nobody should have to know that, or
# remember a command to undo it.
if [ -f install_autostart.sh ]; then
    bash install_autostart.sh >/dev/null 2>&1 || true
fi

}

main "$@"
