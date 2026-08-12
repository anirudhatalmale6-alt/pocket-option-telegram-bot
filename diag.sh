#!/usr/bin/env bash
#
# Diagnose the Pocket Option connection and save the result to a file.
#
#   bash diag.sh
#
# Prints what it finds AND writes the same thing to diagnosis.txt, so it can be
# sent on without needing to select and copy out of a terminal window.
#
# It places no trades. It only reads.

set -uo pipefail       # NOT -e: a failing step is the thing we want reported

cd "$(dirname "$0")"

if [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    echo "Python is not installed. Run:  bash install.sh" >&2
    exit 1
fi

OUT=diagnosis.txt

echo "Running the check — it takes up to a couple of minutes. Please wait."
echo

"$PY" diagnose.py 2>&1 | tee "$OUT"

echo
echo "----------------------------------------------------------------"
echo "Saved to: $(pwd)/$OUT"
echo
echo "To send it to me: open the Files app, click 'Linux files' on the"
echo "left, find diagnosis.txt, and attach it in the chat."
echo "----------------------------------------------------------------"
