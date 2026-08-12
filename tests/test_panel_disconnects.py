"""
A browser going away mid-response must be silent and survivable.

Reported as "I tried to refresh and it closed down". A hard refresh aborts the
request in flight; socketserver's default behaviour is to print a full traceback
to the terminal. The process survives, but to somebody watching that window a
traceback IS a crash — so the appearance was the whole bug.
"""

from __future__ import annotations

import socket
import time
import urllib.request

import pytest

from core.config import BotConfig
from core.web_ui import WebInterface


@pytest.fixture
def panel():
    web = WebInterface(BotConfig(), "127.0.0.1", 0, "")
    web.paper = True
    web.auto_discover = False
    web.start()
    # Port 0 means the OS picked one; ask the server which.
    web.port = web._server.server_address[1]
    time.sleep(0.2)
    yield web
    web.stop()


def abort_midway(port: int) -> None:
    """Send a request, then destroy the connection without reading the reply."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    # SO_LINGER with a zero timeout sends RST rather than a polite FIN — the
    # abrupt teardown a refresh produces.
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                 b"\x01\x00\x00\x00\x00\x00\x00\x00")
    s.close()


def test_the_panel_survives_abandoned_requests(panel, capfd):
    for _ in range(25):
        abort_midway(panel.port)
    time.sleep(0.4)

    with urllib.request.urlopen(
            f"http://127.0.0.1:{panel.port}/api/state", timeout=5) as resp:
        assert resp.status == 200

    # And it did so without printing anything that reads like a crash.
    err = capfd.readouterr().err
    assert "Traceback" not in err


def test_normal_requests_still_work_after_an_abort(panel):
    abort_midway(panel.port)
    time.sleep(0.2)
    with urllib.request.urlopen(f"http://127.0.0.1:{panel.port}/", timeout=5) as resp:
        assert resp.status == 200
        assert b"Pocket Option Bot" in resp.read()
