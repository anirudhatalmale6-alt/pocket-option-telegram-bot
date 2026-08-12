"""
Which version of the code is actually running, and whether newer code is sitting
on disk unused.

Why this exists
---------------
`bash update.sh` downloads new code, but a bot that is already running keeps the
old code in memory until it is restarted. So the files say one thing and the
page in front of you does another — and every symptom of the old bug is still
there after "updating". That has now cost this project two rounds: a feature
gets described, the client updates, does not restart, does not see it, and
reasonably concludes it was never built.

A version string alone does not fix that. What fixes it is comparing the commit
the process STARTED on against the commit on disk right now, and saying so on
the page the client is already looking at.

Reads git's files directly rather than shelling out: this is called from the
status endpoint every couple of seconds, and it must never hang or fail.
"""

from __future__ import annotations

import os
from typing import Optional

# Where the project lives — core/version.py -> core -> project root.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def head_commit(root: Optional[str] = None) -> str:
    """
    The short commit currently checked out, or "" if this is not a git checkout.

    Never raises: a missing .git means someone unpacked a zip, which is fine and
    must not break the panel.
    """
    root = root or ROOT
    head = _read(os.path.join(root, ".git", "HEAD"))
    if not head:
        return ""
    if head.startswith("ref:"):
        ref = head[4:].strip()
        sha = _read(os.path.join(root, ".git", ref))
        if not sha:
            # Packed refs: the loose file is absent once git has packed them.
            for line in _read(os.path.join(root, ".git", "packed-refs")).splitlines():
                if line.endswith(" " + ref):
                    sha = line.split(" ", 1)[0]
                    break
        head = sha
    return head[:7] if head else ""


# Captured once, at import: this is the code the running process is executing.
# It deliberately does NOT update when the files on disk change.
RUNNING = head_commit()


def code_on_disk_is_newer() -> bool:
    """True when the files have been updated but this process predates them."""
    current = head_commit()
    return bool(RUNNING and current and current != RUNNING)
