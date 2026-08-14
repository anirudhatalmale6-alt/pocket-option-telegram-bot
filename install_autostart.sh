#!/usr/bin/env bash
#
# Make the bot come back by itself when Linux starts.
#
#   bash install_autostart.sh
#
# Why this exists
# ---------------
# start.sh detaches the bot from the terminal, so closing the window no longer
# kills it. What nothing inside the container can survive is the container
# itself stopping — which happens every time the Chromebook is shut down, and
# whenever ChromeOS decides to stop Linux to save battery. From the outside the
# only symptom is that localhost stops loading, with nothing running left to
# ask. It has been reported three times as "the localhost website isn't
# working", and every time the fix was to start it again by hand.
#
# So: register the bot to start with the container. Three mechanisms are tried
# because Crostini's differs by ChromeOS version and none of them is present
# everywhere. The first that works wins; the rest are skipped.
#
# Safe to run repeatedly — every step overwrites rather than appends.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
INSTALLED=""

# 1. systemd user service. The best option where it exists: systemd restarts it
#    if it ever dies, not just at boot.
# The venv check is not optional: this unit runs main.py directly, so without
# it systemd would faithfully restart a broken command every 10 seconds for
# ever. start.sh, used by the other two mechanisms, finds a Python for itself
# and says something useful when it cannot.
if [ -x "${HERE}/.venv/bin/python" ] && command -v systemctl >/dev/null 2>&1 &&
        systemctl --user show-environment >/dev/null 2>&1; then
    UNITS="${HOME}/.config/systemd/user"
    mkdir -p "$UNITS"
    cat > "${UNITS}/pocket-option-bot.service" <<EOF
[Unit]
Description=Pocket Option trading bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${HERE}
ExecStart=${HERE}/.venv/bin/python ${HERE}/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
    if systemctl --user daemon-reload >/dev/null 2>&1 &&
       systemctl --user enable pocket-option-bot.service >/dev/null 2>&1; then
        INSTALLED="systemd"
        # Not started here. start.sh may already own the port, and two copies
        # of a trading bot on one account is worse than a slow first start.
    fi
fi

# 2. cron @reboot. Present on most Debian containers even when systemd --user
#    is not reachable.
if [ -z "$INSTALLED" ] && command -v crontab >/dev/null 2>&1; then
    LINE="@reboot /bin/bash ${HERE}/start.sh >/dev/null 2>&1"

    # Read the whole crontab FIRST, then write it. Never
    #     crontab -l | grep -v ... | crontab -
    # Both ends of that pipeline run at once, so the write can truncate the
    # crontab before the read has finished with it, and everything else the
    # user had scheduled is gone. Caught doing exactly that in testing, against
    # a crontab with an unrelated job in it. This is somebody's machine; losing
    # their other cron jobs to install ours is not a trade worth making.
    CURRENT=$(crontab -l 2>/dev/null || true)

    # `|| true` on the filter as well: grep exits non-zero when it passes
    # nothing through, which under `set -e` would kill the script on the one
    # machine that has an empty crontab.
    KEPT=$(printf '%s\n' "$CURRENT" |
           grep -v "pocket-option-telegram-bot/start.sh" |
           grep -v -F "${HERE}/start.sh" || true)

    # Replace any previous line for this bot rather than stacking them up: a
    # crontab with four @reboot entries starts four bots that fight each other
    # over the same account.
    if printf '%s\n%s\n' "$KEPT" "$LINE" | grep -v '^$' | crontab - 2>/dev/null; then
        INSTALLED="cron"
    fi
fi

# 3. The Crostini session's autostart folder. Last because it only fires when
#    the graphical session comes up, which is not guaranteed.
if [ -z "$INSTALLED" ]; then
    AUTO="${HOME}/.config/autostart"
    mkdir -p "$AUTO"
    cat > "${AUTO}/pocket-option-bot.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Pocket Option Bot (autostart)
Exec=/bin/bash "${HERE}/start.sh"
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
    INSTALLED="autostart"
fi

case "$INSTALLED" in
    systemd)   WHERE="a background service that also restarts it if it crashes" ;;
    cron)      WHERE="a scheduled job that runs when Linux starts" ;;
    *)         WHERE="the startup folder" ;;
esac

echo "Done — the bot is registered to start by itself, using ${WHERE}."
echo
echo "This takes effect the NEXT time Linux starts. It does not start the bot"
echo "now, because it may already be running and two copies would fight over"
echo "the same account."
echo
echo "One thing it still cannot do: if the Chromebook is off, nothing runs."
echo "For genuine 24/7 the bot needs a small always-on server — see docs/SETUP.md."
