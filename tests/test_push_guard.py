"""The push guard stops household values and leak patterns in what is about to be pushed."""

import hashlib
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
    lines = ["refs/heads/main abc refs/heads/main def", f"refs/heads/new abc refs/heads/new {zero}",
             f"(delete) {zero} refs/heads/old def"]
    assert push_guard.outgoing(lines, exists=lambda sha: True) == [["def..abc"], ["abc", "--not", "--remotes"]]
    # a force push of rewritten history: the remote's commit is not here, so everything new is checked
    assert push_guard.outgoing(lines[:1], exists=lambda sha: False) == [["abc", "--not", "--remotes"]]


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


def test_personal_categories_join_the_denylist(tmp_path):
    (tmp_path / "c.json").write_text('{"values": [["category", "group", "priority"], ["food", "food", "high"], '
                                     '["alex", "personal", "low"], ["joint", "personal", "low"], '
                                     '["aunt_bea", "family_support", "medium"], ["misc", "other"]]}')
    assert denylist.personal_categories(str(tmp_path / "c.json")) == {"alex", "aunt_bea"}


def test_a_github_address_is_not_a_household_value(tmp_path):
    path = tmp_path / "deny.txt"
    path.write_text("alex\n")
    deny = push_guard.load_denylist(path)
    assert push_guard.denied("pip install git+https://github.com/alex-q/household", *deny) == []
    assert push_guard.denied("alex 20; family", *deny) == ["alex"]


def test_staged_changes_and_messages_are_checked_before_a_commit(tmp_path, monkeypatch):
    work = tmp_path / "work"
    git(tmp_path, "init", "-q", str(work))
    (work / "a.py").write_text("x = 1\n# example: alex 20; family\n")
    git(work, "add", "a.py")
    deny = tmp_path / "deny.txt"
    deny.write_text("alex\n")
    message = tmp_path / "msg"
    message.write_text("Split alex's books\n# Please enter the commit message (alex)\n")
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOUSEHOLD_DENYLIST", str(deny))
    assert push_guard.main([], ["--staged"]) == 1
    assert push_guard.main([], ["--message", str(message)]) == 1
    message.write_text("Split a line\n# Please enter the commit message (alex)\n")
    assert push_guard.main([], ["--message", str(message)]) == 0
    deny.write_text("sam\n")
    assert push_guard.main([], ["--staged"]) == 0


def test_a_binary_file_is_refused_unless_listed_as_a_made_up_fixture(tmp_path, monkeypatch):
    work = tmp_path / "work"
    git(tmp_path, "init", "-q", str(work))
    (work / "statement.pdf").write_bytes(b"%PDF-1.4 not really \x00\xff")
    (work / "tests" / "fixtures").mkdir(parents=True)
    (work / "tests" / "fixtures" / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
    git(work, "add", ".")
    deny = tmp_path / "deny.txt"
    deny.write_text("alex\n")
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOUSEHOLD_DENYLIST", str(deny))
    found = push_guard.check_staged(push_guard.load_denylist(deny))
    assert sorted(f.split(":")[0] for f in found) == ["statement.pdf", "tests/fixtures/chart.png"]
    chart = hashlib.sha256((work / "tests" / "fixtures" / "chart.png").read_bytes()).hexdigest()
    (work / "tests" / "fixtures" / "BINARIES").write_text(f"tests/fixtures/chart.png {chart}  # made up\n")
    git(work, "add", ".")
    assert [f.split(":")[0] for f in push_guard.check_staged(push_guard.load_denylist(deny))] == ["statement.pdf"]
    (work / "tests" / "fixtures" / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n\x01 other")
    git(work, "add", ".")
    found = push_guard.check_staged(push_guard.load_denylist(deny))
    assert any("chart.png" in f and "sha256" in f for f in found)  # same name, other contents: refused


def test_leak_guard_reads_a_listed_pdf_and_refuses_an_unreadable_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    broken = b"%PDF-1.4 broken"
    (tmp_path / "tests" / "fixtures" / "BINARIES").write_text(
        f"tests/fixtures/x.pdf {hashlib.sha256(broken).hexdigest()}\n")
    (tmp_path / "tests" / "fixtures" / "x.pdf").write_bytes(broken)
    assert [n for _, n, _ in push_guard.leak_guard.scan("tests/fixtures/x.pdf")] == ["listed PDF that can't be read"]
    (tmp_path / "y.jpg").write_bytes(b"\xff\xd8\xff")
    assert [n for _, n, _ in push_guard.leak_guard.scan("y.jpg")] == [push_guard.leak_guard.BINARY]


def _pushed(tmp_path, monkeypatch, steps):
    """push_guard.check over commits made by steps, each a function of the work tree, after a first push."""
    work = tmp_path / "work"
    git(tmp_path, "init", "-q", str(work))
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        git(work, "config", k, v)
    (work / "a.py").write_text("x = 1\n")
    git(work, "add", ".")
    git(work, "commit", "-qm", "start")
    base = git(work, "rev-parse", "HEAD")
    for n, step in enumerate(steps):
        step(work)
        git(work, "add", "-A")
        git(work, "commit", "-qm", f"step {n}")
    monkeypatch.chdir(work)
    deny = tmp_path / "deny.txt"
    deny.write_text("alex\n")
    return push_guard.check([[f"{base}..HEAD"]], push_guard.load_denylist(deny))


def test_a_binary_added_then_deleted_in_the_same_push_is_still_refused(tmp_path, monkeypatch):
    found = _pushed(tmp_path, monkeypatch, [
        lambda w: (w / "statement.pdf").write_bytes(b"%PDF-1.4 real \x00\xff"),
        lambda w: (w / "statement.pdf").unlink()])
    assert len(found) == 1 and "statement.pdf" in found[0]


def test_a_binary_replaced_before_the_push_is_checked_in_every_version(tmp_path, monkeypatch):
    def listed(w):
        (w / "tests" / "fixtures").mkdir(parents=True)
        (w / "tests" / "fixtures" / "BINARIES").write_text(f"tests/fixtures/chart.png {'0' * 64}  # made up\n")
        (w / "statement.pdf").write_bytes(b"%PDF-1.4 real \x00\xff")
    found = _pushed(tmp_path, monkeypatch, [
        listed,
        lambda w: (w / "statement.pdf").write_bytes(b"%PDF-1.4 other \x00\xfe")])
    assert len(found) == 2 and all("statement.pdf" in f for f in found)
    assert len({f.split()[1] for f in found}) == 2  # one finding for each commit's version


def test_the_binary_list_allows_only_fixtures_pinned_by_their_hash():
    good = "a" * 64
    text = (f"tests/fixtures/x.png {good}  # made up\n"
            f"docs/real.png {good}\n"            # outside the fixtures: allows nothing
            "tests/fixtures/y.png\n"              # no hash: allows nothing
            f"tests/fixtures/x.png {'b' * 64}\n")  # a second approved version
    assert push_guard.leak_guard.allowed_binaries(lambda p: text) == {"tests/fixtures/x.png": {good, "b" * 64}}


def test_whole_hashes_are_not_account_numbers_but_digits_beside_them_are():
    lg = push_guard.leak_guard
    digits = "".join(str(n % 10) for n in range(1, 11))  # ten digits in a row, built so this file holds none
    sha = digits * 6 + "abcd"
    assert push_guard.pattern_findings("src/x.py", "pinned " + sha) == []
    assert push_guard.pattern_findings("src/x.py", "acct " + digits + " " + sha) != []
    assert lg.without_hashes("x" + sha) == "x" + sha  # part of a longer word is not a hash
