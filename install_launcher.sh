#!/usr/bin/env bash
#
# Put a "Pocket Option Bot" icon in the Chromebook launcher.
#
#   bash install_launcher.sh
#
# Run this once. After that the bot is started by clicking the icon in the
# Chromebook's app list, exactly like any other app — no terminal, no commands
# to remember, no window that has to stay open.
#
# How it works: ChromeOS reads .desktop files out of the container's
# ~/.local/share/applications and shows them in the launcher automatically. The
# absolute path to this folder is baked in at install time, so the icon keeps
# working wherever the bot happens to live.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APPS="${HOME}/.local/share/applications"
DESKTOP="${APPS}/pocket-option-bot.desktop"

mkdir -p "$APPS"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Pocket Option Bot
Comment=Start the trading bot and open its control panel
Exec=/bin/bash "${HERE}/open_panel.sh"
Icon=${HERE}/docs/icon.png
Terminal=true
Categories=Finance;
EOF

chmod +x "$DESKTOP"

# Without this the launcher can take several minutes to notice the new file,
# which reads as "it didn't work" and gets the command run again.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi

echo "Done."
echo
echo "Look in your Chromebook's app list (the circle at the bottom left) for:"
echo
echo "    Pocket Option Bot"
echo
echo "It sits in the 'Linux apps' folder. Drag it to your shelf to keep it handy."
echo "Click it whenever you want the bot — it starts everything and opens the"
echo "control panel by itself."
echo
echo "If it is not there yet, wait a minute or restart Linux:"
echo "    right-click the Terminal icon and choose Shut down Linux, then reopen it."
