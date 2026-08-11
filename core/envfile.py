"""
Read and update the .env file without destroying what is already in it.

Why this exists
---------------
The client is on a Chromebook and has spent a whole day losing fights with
terminals, folder names and hidden files. Asking him to open a dotfile in a text
editor he does not have, to paste a 400-character secret, is a step that was
never going to work. So the control panel writes it for him.

That means writing to .env programmatically, which has to be done carefully:

  * Never rewrite the file wholesale. It has comments explaining every setting,
    and blowing those away turns a documented config into a mystery.
  * Update a key in place if it exists, append if it does not.
  * Do not quote or escape the value — python-dotenv reads a bare line fine, and
    the Pocket Option cookie is URL-encoded so it contains no spaces or quotes.
  * Write via a temporary file and replace, so an interrupted write cannot leave
    a half-written config.
  * chmod 600. This file holds a live session token; it is a password.
"""

from __future__ import annotations

import os
import tempfile
from typing import Dict, Optional

# .env lives next to main.py, i.e. the project root.
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".env")


def read(path: Optional[str] = None) -> Dict[str, str]:
    """Return {KEY: value} for the settings currently in the file."""
    # Resolved here rather than as a default argument: a default binds at import
    # time, so tests (and anything that relocates the file) could never redirect
    # it, and would silently read the real .env instead.
    path = path or ENV_PATH
    out: Dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            out[key.strip()] = val.strip()
    return out


def update(values: Dict[str, str], path: Optional[str] = None) -> None:
    """
    Set each KEY=value, preserving every other line, comment and blank exactly.

    Raises OSError if the file cannot be written — the caller must report that
    rather than claiming the settings were saved.
    """
    path = path or ENV_PATH
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()

    remaining = dict(values)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.partition("=")[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# --- set from the control panel ---")
        for key, val in remaining.items():
            lines.append(f"{key}={val}")

    body = "\n".join(lines).rstrip("\n") + "\n"

    # Write-then-replace: a crash mid-write must not leave a truncated config.
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(tmp, 0o600)          # it holds a session token
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
