"""
Telling the difference between "the fix does not work" and "you are running
code from before the fix".

Twice now a feature has been reported missing because the files were updated
while the bot kept running, serving the old code from memory. The panel has to
detect that itself — asking the client to verify a version by eye is what failed
in the first place.
"""

from __future__ import annotations

import os

from core import version


def make_repo(tmp_path, sha: str, ref: str = "refs/heads/main"):
    """A .git directory with just enough in it to read HEAD."""
    git = tmp_path / ".git"
    (git / os.path.dirname(ref)).mkdir(parents=True, exist_ok=True)
    (git / "HEAD").write_text(f"ref: {ref}\n")
    (git / ref).write_text(sha + "\n")
    return str(tmp_path)


def test_reads_the_checked_out_commit(tmp_path):
    root = make_repo(tmp_path, "abcdef1234567890")
    assert version.head_commit(root) == "abcdef1"


def test_reads_a_detached_head(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("fedcba9876543210\n")
    assert version.head_commit(str(tmp_path)) == "fedcba9"


def test_reads_a_packed_ref(tmp_path):
    # git packs refs periodically; the loose file then does not exist and the
    # naive read returns nothing, which would silently disable the warning.
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        "1111111222222233333334444444555555566666666 refs/heads/main\n")
    assert version.head_commit(str(tmp_path)) == "1111111"


def test_no_git_checkout_is_not_an_error(tmp_path):
    # Someone may have unpacked a zip. That must not break the panel.
    assert version.head_commit(str(tmp_path)) == ""


def test_unreadable_repo_is_not_an_error(tmp_path):
    assert version.head_commit(str(tmp_path / "nope")) == ""


def test_stale_is_false_when_the_commit_has_not_moved(monkeypatch):
    monkeypatch.setattr(version, "RUNNING", "aaaaaaa")
    monkeypatch.setattr(version, "head_commit", lambda root=None: "aaaaaaa")
    assert version.code_on_disk_is_newer() is False


def test_stale_is_true_once_the_files_move_on(monkeypatch):
    monkeypatch.setattr(version, "RUNNING", "aaaaaaa")
    monkeypatch.setattr(version, "head_commit", lambda root=None: "bbbbbbb")
    assert version.code_on_disk_is_newer() is True


def test_no_false_alarm_without_a_git_checkout(monkeypatch):
    # Both sides unknown must mean "no warning", never "everything is stale" —
    # a banner that is always up is a banner nobody reads.
    monkeypatch.setattr(version, "RUNNING", "")
    monkeypatch.setattr(version, "head_commit", lambda root=None: "")
    assert version.code_on_disk_is_newer() is False


def test_the_panel_publishes_both_facts():
    from core.config import BotConfig
    from core.web_ui import WebInterface

    state = WebInterface(BotConfig(), "127.0.0.1", 0, "").state()
    assert "stale_code" in state and isinstance(state["stale_code"], bool)
    assert "version" in state
