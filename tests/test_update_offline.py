"""
`bot update` when the download cannot happen.

This is the one failure the updater cannot treat as an error and stop on. Every
other thing it does is optional — a pull that finds nothing new, a pip install,
a launcher icon — but the person running it has exactly one way to reach their
bot, and it is the panel. An update script that dies before the restart, on a
Chromebook that merely dozed off and lost its DNS, takes that away and reports
it in git's words:

    fatal: unable to access 'https://github.com/...': Could not resolve host

which is true, precise, and tells the reader nothing about what to do or
whether their bot survived. It happened, and the answer came back as a photo of
the terminal.

So: a network failure must exit 0, say what to do in English, and leave a
running bot behind.
"""
import os
import shutil
import socket
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _install(tmp_path, port):
    """A miniature copy of the install, with a stub for anything that starts."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("update.sh", "lib_port.sh"):
        shutil.copy(ROOT / name, repo / name)

    # Stubs, so the test never launches a bot or touches the real machine.
    # Each records the fact of being called, which is what is being asserted.
    for name in ("open_panel.sh", "stop.sh", "install_launcher.sh",
                 "install_autostart.sh", "setup_command.sh"):
        (repo / name).write_text(f'#!/bin/sh\necho ran >> "$PWD/{name}.called"\n')
    (repo / ".env").write_text(f"WEB_PORT={port}\n")

    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(git + ["add", "-A"], cwd=repo, check=True)
    subprocess.run(git + ["commit", "-qm", "x"], cwd=repo, check=True)
    return repo


def _remote(repo, url):
    subprocess.run(["git", "remote", "add", "origin", url], cwd=repo, check=True)


# A hostname that cannot resolve anywhere. `.invalid` is reserved by RFC 2606
# precisely so that tests may rely on it never existing.
UNRESOLVABLE = "https://github.com.this-does-not-exist.invalid/a/b.git"


def _run(repo, tmp_path, offline_name=False):
    """Run the updater. offline_name fakes a container with no DNS."""
    env = dict(os.environ)
    if offline_name:
        # Name lookups fail, but the machine is otherwise on the internet —
        # the exact shape of the Chromebook-woke-up-from-sleep failure.
        binhome = tmp_path / "bin"
        binhome.mkdir(exist_ok=True)
        (binhome / "getent").write_text("#!/bin/sh\nexit 2\n")
        (binhome / "getent").chmod(0o755)
        env["PATH"] = f"{binhome}:{env['PATH']}"
    return subprocess.run(["bash", "update.sh"], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=120)


def test_a_failed_download_is_not_treated_as_a_disaster(tmp_path):
    repo = _install(tmp_path, 8531)
    _remote(repo, UNRESOLVABLE)

    r = _run(repo, tmp_path)

    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "could not reach github.com" in out
    assert "Nothing is broken" in out
    # And it says where the bot is, because that is the thing being reassured
    # about and the reader has to be able to go and look.
    assert "http://localhost:8531" in out


def test_the_bot_is_still_running_afterwards(tmp_path):
    """The whole point. A download that failed must not cost him his panel."""
    repo = _install(tmp_path, 8532)
    _remote(repo, UNRESOLVABLE)

    _run(repo, tmp_path)

    assert (repo / "open_panel.sh.called").exists(), \
        "nothing was listening and the updater did not start the bot"


def test_a_bot_that_is_already_running_is_left_alone(tmp_path):
    """
    The other half of it: do not start a second one.

    Two bots on one Pocket Option account is worse than an update that did not
    land, and a failed pull is never a reason to restart anything — the running
    process is on exactly the code it was on a minute ago.
    """
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        repo = _install(tmp_path, port)
        _remote(repo, UNRESOLVABLE)
        _run(repo, tmp_path)
        assert not (repo / "open_panel.sh.called").exists(), \
            "started a second bot on top of one that was already running"
    finally:
        sock.close()


def test_no_dns_is_named_as_no_dns(tmp_path):
    """
    Restart-Linux is the fix for a container that lost its resolver, and it is
    NOT the fix for Wi-Fi being off. Telling the two apart is the only reason
    the check exists, so the wording has to follow the diagnosis.
    """
    repo = _install(tmp_path, 8533)
    _remote(repo, UNRESOLVABLE)

    r = _run(repo, tmp_path, offline_name=True)

    assert "look up website names" in r.stdout, r.stdout
    assert "Shut down Linux" in r.stdout


def test_the_git_wording_is_still_printed(tmp_path):
    """
    Softened, not hidden. When the friendly paragraph turns out to be the wrong
    guess, the literal error is the only thing that says so — and it is what
    ends up in the photo I get sent.
    """
    repo = _install(tmp_path, 8534)
    _remote(repo, UNRESOLVABLE)

    r = _run(repo, tmp_path)

    assert "Could not resolve host" in r.stdout


def test_a_failure_that_is_not_the_network_still_fails_loudly(tmp_path):
    """
    A conflict or a local edit in the way cannot be smoothed over with a
    paragraph about Wi-Fi, and pretending otherwise would hide a real problem
    behind a reassuring message — which is the failure mode this whole file is
    about, inverted.
    """
    repo = _install(tmp_path, 8535)
    _remote(repo, str(tmp_path / "not-a-repository"))

    r = _run(repo, tmp_path)

    assert r.returncode != 0
    assert "Nothing is broken" not in r.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_script_parses(tmp_path):
    """
    Cheap, and it has caught something before: this script git-pulls a new copy
    of itself while bash is still reading it, which is why the whole body lives
    inside a function. A syntax error here is a bricked updater on someone
    else's machine, with no way to push the fix.
    """
    r = subprocess.run(["bash", "-n", str(ROOT / "update.sh")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_every_script_the_updater_calls_exists():
    """
    The updater ends by running five other scripts. If one is renamed, the
    failure shows up as a silently skipped step — the launcher icon quietly
    stops being refreshed — not as an error anyone would notice.
    """
    body = (ROOT / "update.sh").read_text()
    for name in ("open_panel.sh", "stop.sh", "install_launcher.sh",
                 "install_autostart.sh", "setup_command.sh", "lib_port.sh"):
        if name in body:
            assert (ROOT / name).exists(), f"update.sh calls missing {name}"


def test_the_reassurance_is_not_a_lie(tmp_path):
    """
    It promises the .env is untouched. Check that, rather than trust it: the
    saved cookie lives there and re-fetching one is the single most annoying
    thing this project asks of him.
    """
    repo = _install(tmp_path, 8536)
    _remote(repo, UNRESOLVABLE)
    env = repo / ".env"
    env.write_text(textwrap.dedent("""\
        WEB_PORT=8536
        PO_SSID=a-cookie-that-took-ten-minutes-to-fetch
    """))
    before = env.read_text()

    _run(repo, tmp_path)

    assert env.read_text() == before
