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
  3. the import guard and the tests.

The leak guard's `leak-guard: allow` marker excuses a line from (1), never from (2):
a real value is a leak however it is marked, and no file is exempt from (2), this one included. Without a denylist the push stops;
HOUSEHOLD_NO_DENYLIST=1 pushes without check (2). HOUSEHOLD_PUSH_QUICK=1 skips (3).
tools/denylist.py builds the denylist from exports of the ledger.
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


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


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


def check(ranges, denylist):
    """Findings, as printable lines, for the commits in these ranges."""
    findings = []
    for args in ranges:
        for path, line in added_lines(git("log", "-p", "--format=", "--unified=0", *args)):
            for name in pattern_findings(path, line):
                findings.append(f"{path}: {name}: {line.strip()[:120]}")
            if denylist:  # no file is exempt, this one included
                for hit in denied(line, *denylist):
                    findings.append(f"{path}: household value {hit!r}: {line.strip()[:120]}")
        for message in git("log", "--format=%H%n%B%x1e", *args).split("\x1e"):
            sha, _, body = message.strip().partition("\n")
            for line in body.splitlines():
                names = pattern_findings("", line)
                hits = denied(line, *denylist) if denylist else []
                for what in names + [f"household value {h!r}" for h in hits]:
                    findings.append(f"commit {sha[:7]} message: {what}: {line.strip()[:120]}")
    return findings


def main(stdin_lines):
    path = Path(os.environ.get("HOUSEHOLD_DENYLIST", DENYLIST))
    denylist = None
    if path.exists():
        denylist = load_denylist(path)
    elif os.environ.get("HOUSEHOLD_NO_DENYLIST") != "1":
        print(f"push guard: no denylist at {path}. Build one with tools/denylist.py, "
              "or push with HOUSEHOLD_NO_DENYLIST=1 to skip that check.")
        return 1
    findings = check(outgoing(stdin_lines), denylist)
    if findings:
        print("push guard: the push would publish household data:")
        for line in findings:
            print("  " + line)
        print("Fix the commits (amend or rebase) and push again.")
        return 1
    if os.environ.get("HOUSEHOLD_PUSH_QUICK") != "1":
        for command in ([sys.executable, "tools/import_guard.py"], [sys.executable, "-m", "pytest", "-q"]):
            if subprocess.run(command).returncode:
                print(f"push guard: {' '.join(command)} failed")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin.read().splitlines()))
