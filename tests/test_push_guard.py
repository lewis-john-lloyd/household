"""The push guard stops household values and leak patterns in what is about to be pushed."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import denylist  # noqa: E402
import push_guard  # noqa: E402

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def test_amounts_are_found_however_they_are_written():
    text = 'a -1,234.56 b "2 000 00 12 345 67" c 99.10 d 1.5 e 2026-01-02 f v0.8.9'
    assert push_guard.amounts_in(text) == {"1234.56", "2000.00", "12345.67", "99.10"}


def test_denylist_words_match_whole_words_and_amounts_any_form(tmp_path):
    path = tmp_path / "deny.txt"
    path.write_text("# comment\nAcme Widgets\n4321\n1234.56\n")
    deny = push_guard.load_denylist(path)
    assert push_guard.denied("paid by ACME WIDGETS, LLC", *deny) == ["Acme Widgets"]
    assert push_guard.denied("card 4321 and 14321", *deny) == ["4321"]
    assert push_guard.denied('net "1 234 56"', *deny) == ["1234.56"]
    assert push_guard.denied("1234.57 and acmewidgets", *deny) == []


def test_added_lines_and_outgoing_ranges():
    diff = "+++ b/src/x.py\n@@ -1 +1 @@\n-old\n+new line\n+++ /dev/null\n+gone\n"
    assert push_guard.added_lines(diff) == [("src/x.py", "new line"), ("", "gone")]
    zero = push_guard.ZERO
    lines = [f"refs/heads/main abc refs/heads/main def", f"refs/heads/new abc refs/heads/new {zero}",
             f"(delete) {zero} refs/heads/old def"]
    assert push_guard.outgoing(lines) == [["def..abc"], ["abc", "--not", "--remotes"]]


def test_denylist_builder_keeps_amounts_with_cents(tmp_path):
    (tmp_path / "t.csv").write_text("amount,note\n1234.56,x\n-250.10,y\n500.00,round\n12.34,small\n")
    (tmp_path / "t.json").write_text('{"values": [["amount"], ["4,321.09"]]}')
    assert denylist.amounts([str(tmp_path / "t.csv"), str(tmp_path / "t.json")]) == {"1234.56", "250.10", "4321.09"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def test_a_push_with_a_household_value_is_stopped(tmp_path, monkeypatch):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(tmp_path, "init", "-q", str(work))
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        git(work, "config", k, v)
    git(work, "remote", "add", "origin", str(remote))
    (work / "a.py").write_text("x = 1\n")
    git(work, "add", "a.py")
    git(work, "commit", "-qm", "start")
    git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    base = git(work, "rev-parse", "HEAD")
    (work / "a.py").write_text("x = 1\n# example: 1 234 56\n")
    git(work, "commit", "-qam", "add an example")
    head = git(work, "rev-parse", "HEAD")
    deny = tmp_path / "deny.txt"
    deny.write_text("1234.56\n")
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOUSEHOLD_DENYLIST", str(deny))
    monkeypatch.setenv("HOUSEHOLD_PUSH_QUICK", "1")
    stdin = [f"refs/heads/main {head} refs/heads/main {base}"]
    assert push_guard.main(stdin) == 1
    deny.write_text("9999.99\n")
    assert push_guard.main(stdin) == 0
    monkeypatch.setenv("HOUSEHOLD_DENYLIST", str(tmp_path / "missing.txt"))
    assert push_guard.main(stdin) == 1  # no denylist, no push
