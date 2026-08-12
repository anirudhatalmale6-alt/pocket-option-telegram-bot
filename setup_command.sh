#!/usr/bin/env bash
#
# Install a one-word `bot` command, so starting the bot never depends on which
# folder the terminal happens to be in.
#
#   bash setup_command.sh
#
# Then, from anywhere, in any new terminal:
#
#   bot            # practice mode
#   bot --live     # use the account in your .env
#
# Why this file exists: every new terminal tab opens in the home folder, and
# `bash run.sh` only works from the project folder. That difference has now cost
# this project two rounds — the error ("No such file or directory") describes
# what bash could not find, not what the person did wrong, and the fix ("cd
# somewhere first") is invisible unless you already know it.
#
# A shell function does not care where you are. It also cannot be pasted wrong:
# it is three letters.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
RC="$HOME/.bashrc"
BEGIN="# >>> pocket option bot >>>"
END="# <<< pocket option bot <<<"

if [ ! -f "$HERE/run.sh" ]; then
    echo "Cannot find run.sh next to this script — run it from the bot's folder." >&2
    exit 1
fi

touch "$RC"

# Remove any previous block first, so re-running this after moving the folder
# updates the path instead of leaving two definitions fighting each other.
if grep -qF "$BEGIN" "$RC" 2>/dev/null; then
    TMP="$(mktemp)"
    awk -v b="$BEGIN" -v e="$END" '
        $0 == b {skip = 1}
        skip != 1 {print}
        $0 == e {skip = 0}
    ' "$RC" > "$TMP"
    mv "$TMP" "$RC"
    echo "Updated the existing shortcut."
fi

cat >> "$RC" <<EOF
$BEGIN
# Start the Pocket Option bot from anywhere. Practice by default: 'bot --live'
# is the only way to reach a real account, so it can never happen by accident.
bot() {
    if [ "\${1:-}" = "--live" ]; then
        shift
        ( cd "$HERE" && bash run.sh "\$@" )
    else
        ( cd "$HERE" && bash run.sh --paper "\$@" )
    fi
}
$END
EOF

echo
echo "Done. The command is installed."
echo
echo "Close this terminal window, open a new one, and type just:"
echo
echo "    bot"
echo
echo "That starts the bot in practice mode from wherever you are."
echo "It does NOT work in this window — a terminal only reads the setting when"
echo "it opens, so this one still knows nothing about it. Open a new one."
echo
