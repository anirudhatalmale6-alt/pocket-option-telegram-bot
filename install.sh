#!/usr/bin/env bash
#
# One-shot installer for the Pocket Option bot on Debian/Ubuntu — including the
# Linux environment on a Chromebook (Crostini), which is what this was written
# for. Safe to run more than once.
#
#   bash install.sh
#
# It installs Python, creates an isolated virtual environment, installs the
# bot's dependencies, and writes a starter .env if you do not have one. It does
# NOT start trading and does NOT need your Pocket Option details to run.

set -euo pipefail

BOLD=$(printf '\033[1m'); GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m')
RESET=$(printf '\033[0m')

say()  { echo "${BOLD}==>${RESET} $*"; }
ok()   { echo "${GREEN}  ok${RESET} $*"; }
warn() { echo "${YELLOW}  ! ${RESET} $*"; }

cd "$(dirname "$0")"

say "Checking for Python..."
if ! command -v python3 >/dev/null 2>&1 || ! python3 -m venv --help >/dev/null 2>&1; then
    say "Installing Python (you may be asked for your password)..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-venv python3-pip git
fi
ok "$(python3 --version)"

say "Creating the virtual environment (.venv)..."
[ -d .venv ] || python3 -m venv .venv
ok "virtual environment ready"

say "Installing the bot's dependencies (this takes a minute or two)..."
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt
ok "dependencies installed"

if [ ! -f .env ]; then
    say "Creating your settings file (.env)..."
    cp .env.example .env
    ok "created .env — open it later to paste your Pocket Option token"
else
    warn ".env already exists, leaving it exactly as it is"
fi

say "Running the tests to prove the install is sound..."
./.venv/bin/pip install --quiet pytest
./.venv/bin/python -m pytest tests/ -q

cat <<EOF

${GREEN}${BOLD}Done.${RESET}

Two things you can do right now, neither needs an account:

  ${BOLD}1. See which pairs pay best today${RESET}
     ./.venv/bin/python scan_assets.py

  ${BOLD}2. Start the control panel on the practice simulator${RESET}
     ./.venv/bin/python main.py --paper

     Then open ${BOLD}http://localhost:8080${RESET} in Chrome and press START.
     If that address does not load, try ${BOLD}http://penguin.linux.test:8080${RESET}

  Press Ctrl+C in this window to stop the bot.

When you are ready to trade on your Pocket Option demo, put your token in the
.env file (see docs/SETUP.md) and run it without --paper.
EOF
