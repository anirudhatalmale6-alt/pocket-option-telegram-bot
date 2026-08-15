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
#
# 'bot update' is here for the same reason the whole function is: updating meant
# 'cd ~/pocket-option-telegram-bot && bash update.sh', which is a folder name to
# remember, a word ('bash') that is easy to leave off, and two failure messages
# that describe bash's problem rather than the person's. Leaving off 'bash'
# gives "command not found"; being in the wrong folder gives "No such file or
# directory". Neither says "you are in the wrong place" or "put bash in front".
# Reported three times now, so the command stops depending on either.
bot() {
    case "\${1:-}" in
        update|--update)
            ( cd "$HERE" && bash update.sh )
            ;;
        --live)
            shift
            ( cd "$HERE" && bash run.sh "\$@" )
            ;;
        *)
            ( cd "$HERE" && bash run.sh --paper "\$@" )
            ;;
    esac
}
$END
EOF

echo
echo "Done. The command is installed."
echo
echo "Close this terminal window, open a new one, and type just:"
echo
echo "    bot            - start it (practice mode)"
echo "    bot update     - get the latest version"
echo "    bot --live     - use the account in your .env"
echo
echo "All three work from wherever you are — no folder to find first."
echo "It does NOT work in this window — a terminal only reads the setting when"
echo "it opens, so this one still knows nothing about it. Open a new one."
echo
