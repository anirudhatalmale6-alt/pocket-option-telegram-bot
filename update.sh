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

cat <<EOF

${GREEN}${BOLD}Done.${RESET} Now start it with:

    bash run.sh --paper      (practice, no account)
    bash run.sh              (your account, using .env)

One thing that catches people out: your browser caches the control panel, so
after an update press ${BOLD}Ctrl+Shift+R${RESET} on the page to force a fresh copy.
Without that you can be looking at the old panel and think nothing changed.
EOF
