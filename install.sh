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
if ! command -v python3 >/dev/null 2>&1; then
    say "Installing Python (you may be asked for your password)..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-venv python3-pip git
fi
ok "$(python3 --version)"

# Debian/ChromeOS notes, learned the hard way on a real Chromebook:
#   * `python3 -m venv --help` succeeds even when the python3-venv package is
#     missing, so a --help preflight proves nothing.
#   * A failed creation still leaves a .venv DIRECTORY behind, so testing for
#     the directory makes every later run skip the repair it needs.
#   * Worst of all, creation can SUCCEED and still give you a venv with no pip
#     in it (Debian ships ensurepip separately). Every later step then dies on
#     "No module named pip".
# So the only check worth trusting is: can this environment actually run pip?
venv_ok() {
    [ -x .venv/bin/python ] && .venv/bin/python -m pip --version >/dev/null 2>&1
}

say "Creating the virtual environment (.venv)..."
if ! venv_ok; then
    if [ -e .venv ]; then
        warn "found an unusable .venv from an earlier attempt — rebuilding it"
        rm -rf .venv
    fi
    python3 -m venv .venv >/dev/null 2>&1 || true
fi

if ! venv_ok; then
    say "Installing the Python venv/pip packages (you may be asked for your password)..."
    PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "3")
    sudo apt-get update -qq || true
    # The versioned name is the one that actually matters on Debian testing.
    sudo apt-get install -y -qq python3-venv python3-pip "python${PYV}-venv" || \
        sudo apt-get install -y -qq python3-venv python3-pip || true
    rm -rf .venv
    python3 -m venv .venv >/dev/null 2>&1 || true
fi

if ! venv_ok && [ -x .venv/bin/python ]; then
    warn "environment has no pip — bootstrapping it"
    .venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

if ! venv_ok && [ -x .venv/bin/python ]; then
    # Last resort: fetch pip's official bootstrap script.
    warn "fetching pip directly"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py 2>/dev/null || true
    elif command -v wget >/dev/null 2>&1; then
        wget -qO /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py 2>/dev/null || true
    fi
    [ -s /tmp/get-pip.py ] && .venv/bin/python /tmp/get-pip.py >/dev/null 2>&1 || true
    rm -f /tmp/get-pip.py
fi

if ! venv_ok; then
    echo >&2
    echo "Could not build a working Python environment." >&2
    echo "Please send me a photo of this whole window and I will sort it." >&2
    exit 1
fi
ok "virtual environment ready (pip $(.venv/bin/python -m pip --version | cut -d' ' -f2))"

say "Installing the bot's dependencies (this takes a minute or two)..."
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
ok "dependencies installed"

if [ ! -f .env ]; then
    say "Creating your settings file (.env)..."
    cp .env.example .env
    ok "created .env — open it later to paste your Pocket Option token"
else
    warn ".env already exists, leaving it exactly as it is"
fi

say "Running the tests to prove the install is sound..."
.venv/bin/python -m pip install --quiet pytest
.venv/bin/python -m pytest tests/ -q

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
