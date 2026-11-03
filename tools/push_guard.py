"""Check a push before it leaves this machine, so household data never reaches this public repo.

git runs this from .githooks/pre-push (turned on once per clone with
`git config core.hooksPath .githooks`). For the commits being pushed it checks:

  1. every added line and every commit message against the leak guard's patterns
     (account-number-shaped digits, dollar amounts outside fixtures, Drive IDs, secrets);
  2. the same text against a private denylist of the household's real values, kept
     outside the repo (DENYLIST, or the path in $HOUSEHOLD_DENYLIST). An entry is a
     word or phrase (names, streets, employers, account digits; matched as whole words,
     ignoring case) or an amount with cents, matched however it is written: 1234.56,
     1,234.56, a dollar sign before it, or the digit groups of a PDF's text, "1 234 56";
  3. binary files (PDFs, images, archives, office files): refused unless listed in
     tests/fixtures/BINARIES, and a listed PDF's text goes through (1) and (2);
  4. the import guard and the tests.

The leak guard's `leak-guard: allow` marker excuses a line from (1), never from (2):
a real value is a leak however it is marked, and no file is exempt from (2), this one included. Without a denylist the push stops;
HOUSEHOLD_NO_DENYLIST=1 pushes without check (2). HOUSEHOLD_PUSH_QUICK=1 skips (4).
tools/denylist.py builds the denylist from exports of the ledger.

The same checks, (1) to (3), run earlier so a value never gets into a commit:
`--staged` checks the staged changes (.githooks/pre-commit) and `--message FILE` a
commit message (.githooks/commit-msg). The push check stays as the backstop.
GitHub addresses are public, so a name inside one (the repo's owner) is not a find.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import leak_guard  # noqa: E402

DENYLIST = Path.home() / ".config" / "household" / "denylist.txt"
ZERO = "0" * 40
AMOUNT_ENTRY = re.compile(r"^-?\d+\.\d\d$")
# 1234.56, 1,234.56, -1 234 56, with or without a dollar sign: a dollar part (digits, or groups of three) and two cents digits
AMOUNT_TEXT = re.compile(r"(?<![\w.])-?\$?(\d{1,3}(?:[, ]\d{3})+|\d+)[. ](\d{2})(?![\d])")
GITHUB = re.compile(r"(?:https?://)?github\.com/[\w.-]+(?:/[\w.-]+)?")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def git_bytes(*args):
    """A git command's output as bytes, or None when it fails (a file deleted at that commit)."""
    done = subprocess.run(["git", *args], capture_output=True)
    return done.stdout if done.returncode == 0 else None


def load_denylist(path):
    """(amounts, word patterns) from a denylist file: one entry per line, # for comments."""
    amounts, words = set(), []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        if AMOUNT_ENTRY.match(entry):
            amounts.add(f"{abs(float(entry)):.2f}")
        else:
            words.append((entry, re.compile(rf"(?<!\w){re.escape(entry)}(?!\w)", re.IGNORECASE)))
    return amounts, words


def amounts_in(text):
    """Every amount with cents in the text, as "1234.56"."""
    out = set()
    for m in AMOUNT_TEXT.finditer(text):
        out.add(f"{int(re.sub(r'[, ]', '', m.group(1)))}.{m.group(2)}")
    return out


def denied(text, amounts, words):
    """The denylist entries this text contains."""
    text = GITHUB.sub(" ", text)
    hits = [entry for entry, pattern in words if pattern.search(text)]
    return hits + sorted(amounts_in(text) & amounts)


def outgoing(lines):
    """git log range arguments for each ref git says it is pushing (stdin of pre-push)."""
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:  # a deletion pushes nothing
            continue
        local, remote = parts[1], parts[3]
        out.append([local, "--not", "--remotes"] if remote == ZERO else [f"{remote}..{local}"])
    return out


def added_lines(diff):
    """(path, line) for each line a `git log -p` diff adds."""
    path, out = "", []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("+") and not line.startswith("+++"):
            out.append((path, line[1:]))
    return out


def pattern_findings(path, line):
    if leak_guard.ALLOW_MARKER in line or path.endswith("leak_guard.py"):
        return []
    in_fixtures = path.startswith(leak_guard.FIXTURE_DIR)
    return [name for name, pattern, in_fix in leak_guard.CHECKS
            if (in_fix or not in_fixtures) and pattern.search(line)]


def line_findings(path, line, denylist):
    """Findings for one added line of a file."""
    out = [f"{path}: {name}: {line.strip()[:120]}" for name in pattern_findings(path, line)]
    if denylist:  # no file is exempt, this one included
        out += [f"{path}: household value {hit!r}: {line.strip()[:120]}" for hit in denied(line, *denylist)]
    return out


def message_findings(label, body, denylist):
    """Findings for a commit message (git's # comment lines left out)."""
    out = []
    for line in body.splitlines():
        if line.startswith("#"):
            continue
        hits = denied(line, *denylist) if denylist else []
        for what in pattern_findings("", line) + [f"household value {h!r}" for h in hits]:
            out.append(f"{label}: {what}: {line.strip()[:120]}")
    return out


def binary_paths(numstat):
    """Paths git counts as binary in `--numstat` output ("-<tab>-<tab>path")."""
    return sorted({line.split("\t", 2)[2] for line in numstat.splitlines() if line.startswith("-\t-\t")})


def binary_findings(paths, read, denylist):
    """Findings for binary files: refused unless listed in tests/fixtures/BINARIES; a listed PDF's text
    goes through the denylist too. read(path) -> bytes, or None for a file that no longer exists."""
    allowed = leak_guard.allowed_binaries(lambda p: (read(p) or b"").decode("utf-8", "replace"))
    findings = []
    for path in paths:
        data = read(path)
        if data is None:
            continue
        for _, name, text in leak_guard.binary_findings(path, data, allowed):
            findings.append(f"{path}: {name}: {text.strip()[:120]}")
        if denylist and path in allowed and path.lower().endswith(".pdf"):
            for hit in denied(leak_guard.pdf_text(data) or "", *denylist):
                findings.append(f"{path}: household value {hit!r} in the PDF's text")
    return findings


def check(ranges, denylist):
    """Findings, as printable lines, for the commits in these ranges."""
    findings = []
    for args in ranges:
        for path, line in added_lines(git("log", "-p", "--format=", "--unified=0", *args)):
            findings += line_findings(path, line, denylist)
        for message in git("log", "--format=%H%n%B%x1e", *args).split("\x1e"):
            sha, _, body = message.strip().partition("\n")
            findings += message_findings(f"commit {sha[:7]} message", body, denylist)
        tip = args[0].split("..")[-1]
        paths = binary_paths(git("log", "--numstat", "--format=", *args))
        findings += binary_findings(paths, lambda p, tip=tip: git_bytes("show", f"{tip}:{p}"), denylist)
    return findings


def check_staged(denylist):
    """Findings for the changes staged for the next commit."""
    findings = []
    for path, line in added_lines(git("diff", "--cached", "--unified=0")):
        findings += line_findings(path, line, denylist)
    paths = binary_paths(git("diff", "--cached", "--numstat"))
    return findings + binary_findings(paths, lambda p: git_bytes("show", f":{p}"), denylist)


def _denylist(what):
    """(denylist or None, stop): the private denylist, or why there is none."""
    path = Path(os.environ.get("HOUSEHOLD_DENYLIST", DENYLIST))
    if path.exists():
        return load_denylist(path), False
    if os.environ.get("HOUSEHOLD_NO_DENYLIST") == "1":
        return None, False
    print(f"push guard: no denylist at {path}. Build one with tools/denylist.py, "
          f"or {what} with HOUSEHOLD_NO_DENYLIST=1 to skip that check.")
    return None, True


def _report(findings, what, fix):
    if findings:
        print(f"push guard: {what} household data:")
        for line in findings:
            print("  " + line)
        print(fix)
    return 1 if findings else 0


def main(stdin_lines, argv=()):
    argv = list(argv)
    if argv[:1] == ["--staged"]:
        denylist, stop = _denylist("commit")
        return 1 if stop else _report(check_staged(denylist), "the commit would hold",
                                      "Change it to a made-up value, stage it again and commit.")
    if argv[:1] == ["--message"] and len(argv) == 2:
        denylist, stop = _denylist("commit")
        body = Path(argv[1]).read_text(encoding="utf-8")
        return 1 if stop else _report(message_findings("commit message", body, denylist),
                                      "the commit message holds", "Reword the message and commit again.")
    denylist, stop = _denylist("push")
    if stop:
        return 1
    if _report(check(outgoing(stdin_lines), denylist), "the push would publish",
               "Fix the commits (amend or rebase) and push again."):
        return 1
    if os.environ.get("HOUSEHOLD_PUSH_QUICK") != "1":
        for command in ([sys.executable, "tools/import_guard.py"], [sys.executable, "-m", "pytest", "-q"]):
            if subprocess.run(command).returncode:
                print(f"push guard: {' '.join(command)} failed")
                return 1
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main([] if args else sys.stdin.read().splitlines(), args))
