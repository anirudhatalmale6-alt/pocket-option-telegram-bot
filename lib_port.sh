#!/usr/bin/env bash
#
# One answer to "is the control panel's port free?", shared by every script
# that needs to know.
#
#   . lib_port.sh          # sets PY and PORT, defines port_state / listening
#
# Why this file exists
# --------------------
# The same eleven-line probe was pasted into update.sh, open_panel.sh and
# start.sh. It was wrong in the same way in all three, and fixing it in one
# left the other two to produce the same failure through a different door:
#
#   ==> Starting it on the new code...
#   Already running.
#   Done — the bot is running the new code and the panel is opening.
#
# with nothing running at all, and "localhost can't be reached" straight after.
#
# What was wrong with it
# ----------------------
# 1. It bound WITHOUT SO_REUSEADDR. The bot's own server sets
#    allow_reuse_address (ThreadingHTTPServer does by default), so when the bot
#    stops with a browser tab still open on the panel, the leftover connections
#    hold the address in TIME_WAIT for up to a minute — the probe says "busy",
#    the bot would bind perfectly well, and the two disagree for the whole
#    minute. Reproduced exactly; it is what produced "the new code downloaded
#    but the bot would not stop" followed by "localhost can't be reached".
#
#    The question worth asking is "could the BOT bind this?", so the probe has
#    to use the bot's own socket options. Anything else answers a different
#    question and is entitled to a different answer.
#
# 2. It had two outcomes when there are three. Any failure to run the probe at
#    all — no python, a WEB_PORT that will not parse — exited non-zero and was
#    read as "busy", permanently and invisibly.
#
# 3. WEB_PORT was taken as-is. A duplicated line, or a comment on the same
#    line, produced something that is not a port number, and see (2).

# The .env may legitimately not exist yet; 8080 is the documented default.
# Deliberately only the FIRST match and only the digits: a duplicated WEB_PORT
# line used to concatenate into one absurd number, and a trailing comment used
# to be swallowed into the value.
PORT=$(grep -E '^WEB_PORT=' .env 2>/dev/null | head -n 1 | cut -d= -f2 |
       tr -cd '0-9' || true)
PORT=${PORT:-8080}

# Never clobber a PY the calling script already worked out — start.sh picks its
# interpreter with more care than this (and checks its dependencies), and
# quietly replacing it here would undo that.
if [ -z "${PY:-}" ]; then
    if [ -x .venv/bin/python ]; then
        PY=.venv/bin/python
    else
        PY=python3
    fi
fi

# free | busy | unknown
port_state() {
    "$PY" - "$PORT" <<'PYEOF' 2>/dev/null
import socket, sys
try:
    port = int(sys.argv[1])
    if not 0 < port < 65536:
        raise ValueError(port)
except ValueError:
    sys.exit(3)
sock = socket.socket()
# Exactly what the bot's own server does.
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
sys.exit(0)
PYEOF
    case $? in
        0) echo free ;;
        1) echo busy ;;
        *) echo unknown ;;
    esac
}

# "Something is really there." Note that `unknown` is false here: not knowing
# is not evidence, and every caller treats it as its own case.
listening() { [ "$(port_state)" = busy ]; }
