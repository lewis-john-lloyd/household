"""Reject commits that could leak household data into this public repo.

Scans every tracked file (or the paths given) for account-number-shaped digit
runs, dollar amounts outside test fixtures, Google document IDs and URLs, and
common secret formats. A line can opt out with the marker `leak-guard: allow`,
which should be rare and obvious in review.

Exit status is 1 when anything is found.
"""

import re
import subprocess
import sys
from pathlib import Path

ALLOW_MARKER = "leak-guard: allow"
FIXTURE_DIR = "tests/fixtures/"
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".lock"}

CHECKS = [
    ("account-number-shaped digit run", re.compile(r"(?<![\d.])\d{8,}(?![\d.])"), True),
    ("dollar amount", re.compile(r"\$\s?\d[\d,]*(\.\d{2})?"), False),
    ("Google document URL", re.compile(r"docs\.google\.com/\S+/d/[\w-]{20,}"), True),
    ("Google Drive URL", re.compile(r"drive\.google\.com/\S+"), True),
    ("Google API key", re.compile(r"AIza[\w-]{35}"), True),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), True),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}"), True),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), True),
]


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def scan(path):
    """Return (line number, check name, line) for each finding in one file."""
    if Path(path).suffix.lower() in SKIP_SUFFIXES or path.endswith("leak_guard.py"):
        return []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return []
    in_fixtures = path.replace("\\", "/").startswith(FIXTURE_DIR)
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for name, pattern, applies_in_fixtures in CHECKS:
            if in_fixtures and not applies_in_fixtures:
                continue
            if pattern.search(line):
                findings.append((number, name, line.strip()))
    return findings


def main(paths):
    failed = False
    for path in paths or tracked_files():
        for number, name, line in scan(path):
            failed = True
            print(f"{path}:{number}: {name}: {line[:120]}")
    if failed:
        print("\nleak guard: possible household data found; remove it, or mark an intended "
              f"line with '{ALLOW_MARKER}'.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
