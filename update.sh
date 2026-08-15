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
HERE=$(pwd)

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

. ./lib_port.sh              # PORT, PY, port_state, listening

# Pulling new code does nothing to a bot that is already running: Python loaded
# the old files at startup and will keep using them. Refreshing the browser does
# not help either, because the page is served by that same old process. So stop
# it — and then start it again unconditionally, below.
UNIT=pocket-option-bot.service

systemd_running() {
    command -v systemctl >/dev/null 2>&1 &&
        systemctl --user is-active --quiet "$UNIT" 2>/dev/null
}

# The process ids of bots started from THIS directory, and nothing else.
#
# Two conditions, both required: the process must be running main.py AND its
# working directory must be this install. Either alone is not enough —
# `pgrep -f main.py` would match every copy of the bot on the machine plus
# anything else of the user's with those five characters in its command line,
# and a directory match alone would catch this script.
#
# Matched through /proc rather than by pattern because start.sh launches it as
# `python main.py` (relative) while the systemd unit uses the absolute path, so
# there is no one string that matches both. The working directory is the same
# either way.
# Matched with a bash `case`, NOT by piping into grep. `grep main.py` run from
# this directory is itself a process whose working directory is this directory
# and whose command line contains "main.py" — so the search matched its own
# helper. Caught in testing. Harmless when it kills one, fatal to the check
# afterwards: "has it stopped yet?" could never come back empty, so a bot that
# had shut down perfectly would be reported as refusing to stop. Which is the
# exact bug this whole change is about, reintroduced one layer down.
# Compared argument by argument rather than by searching the command line as
# one string. A substring search matches any process that merely MENTIONS
# main.py — including a shell running a script with those characters in it, and
# including this search's own helper processes. Requiring argv[0] to be a
# python and an actual argument to be main.py cannot match those.
ours() {
    local pid argv a hit
    for d in /proc/[0-9]*; do
        pid=${d#/proc/}
        [ "$pid" = "$$" ] && continue
        [ "$(readlink -f "$d/cwd" 2>/dev/null)" = "$HERE" ] || continue
        mapfile -d '' -t argv < "$d/cmdline" 2>/dev/null || continue
        [ "${#argv[@]}" -ge 2 ] || continue
        case "${argv[0]}" in *python*) ;; *) continue ;; esac
        hit=no
        for a in "${argv[@]:1}"; do
            case "$a" in main.py|*/main.py) hit=yes ;; esac
        done
        if [ "$hit" = yes ]; then echo "$pid"; fi
    done
}

# Wait for the port to actually come free. `kill` returning 0 only means the
# signal was delivered.
wait_until_free() {
    for _ in $(seq 1 20); do
        listening || return 0
        sleep 0.5
    done
    return 1
}

STOPPED=yes
if systemd_running; then
    # Started by the autostart service, so there is no bot.pid and stop.sh
    # cannot touch it — it would report "nothing was started with start.sh",
    # exit 0, and this script would go on to claim the new code was running
    # while the old process kept serving the panel. Ask systemd instead.
    echo "${BOLD}==>${RESET} Restarting the background service..."
    systemctl --user restart "$UNIT" >/dev/null 2>&1 || true
    # Not wait_until_free: systemd brings it straight back up on the new code,
    # so the port being busy again is success here, not failure. Give it a
    # moment to rebind, then let open_panel.sh below find it already running.
    for _ in $(seq 1 20); do
        listening && break
        sleep 0.5
    done
elif listening; then
    echo "${BOLD}==>${RESET} Stopping the old version..."
    # Deliberately not `stop.sh && start.sh`. Chaining them on && means any
    # non-zero exit from the stop leaves the bot down and never starts it back
    # up, which is the worst possible outcome for a script whose entire job is
    # to leave you running the new code.
    bash stop.sh >/dev/null 2>&1 || true
    # Verify. stop.sh works from bot.pid alone, so anything started another way
    # — by hand with run.sh, or by a service — survives it and stop.sh still
    # exits 0. Without this check the green "Done" below is simply false, and
    # the next report is "the update didn't change anything".
    if ! wait_until_free; then
        # stop.sh could not reach it, so find it the other way: a process
        # running THIS directory's main.py. Matched on the absolute path so it
        # can only ever be this install — never another copy of the bot, and
        # never anything else on the machine. Asked politely first; stop.sh
        # already explains why a clean shutdown matters to the broker
        # connection.
        echo "    it is not the one in bot.pid — looking for it by name..."
        PIDS=$(ours 2>/dev/null || true)
        if [ -n "$PIDS" ]; then
            kill $PIDS 2>/dev/null || true
            wait_until_free || { kill -9 $PIDS 2>/dev/null || true; }
        fi
        wait_until_free || STOPPED=no
    fi
elif [ "$(port_state)" = unknown ]; then
    # The port could not be read at all, so the socket cannot answer anything.
    # Fall back to the process list, which does not depend on WEB_PORT parsing
    # or on python being usable. Without this, an unreadable port would skip
    # the stop entirely and start a second bot on top of the first.
    if [ -n "$(ours 2>/dev/null || true)" ]; then
        echo "${BOLD}==>${RESET} Stopping the old version..."
        bash stop.sh >/dev/null 2>&1 || true
        PIDS=$(ours 2>/dev/null || true)
        if [ -n "$PIDS" ]; then
            kill $PIDS 2>/dev/null || true
            for _ in $(seq 1 20); do
                [ -z "$(ours 2>/dev/null || true)" ] && break
                sleep 0.5
            done
        fi
        [ -z "$(ours 2>/dev/null || true)" ] || STOPPED=no
    fi
fi

if [ "$STOPPED" = no ]; then
    # Something really is holding the port. Do NOT start a second copy: two
    # bots on one Pocket Option account is worse than an update that did not
    # land, and this is the one case where stopping is the right answer.
    #
    # Note what this branch may NOT do any more, though. It used to be reached
    # whenever the probe said "busy", and it exited here — which left NOTHING
    # running and produced "the new code downloaded but the bot would not stop"
    # followed straight away by "localhost can't be reached". The bot had
    # stopped perfectly well; a browser tab still open on the panel had left
    # the address in TIME_WAIT and the probe called that busy. Refusing to
    # start was the only thing actually broken. The probe now asks the same
    # question the bot's own socket asks, so reaching this line means a real
    # process, not a ghost.
    cat <<EOF

${YELLOW}${BOLD}The new code downloaded, but something is still using port ${PORT}.${RESET}

I have not started the bot, because if that something IS the old bot then
starting another would put two of them on your account at once.

Close any terminal window that has the bot running in it and press Ctrl+C
there, then run this again. If there is no such window, restart Linux:
right-click the Terminal icon, choose ${BOLD}Shut down Linux${RESET}, and reopen it.
EOF
    exit 1
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

# And the `bot` shell shortcut, for the same reason: it is a block of code
# living in ~/.bashrc, so it goes stale the moment this file learns a new
# subcommand. Refreshing it here means `bot update` keeps working, and keeps
# meaning the current thing, without anyone having to be told to re-run an
# installer they have already forgotten about. Re-running replaces the marked
# block rather than appending a second definition.
if [ -f setup_command.sh ]; then
    bash setup_command.sh >/dev/null 2>&1 || true
fi

}

main "$@"
