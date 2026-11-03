"""Reject commits that could leak household data into this public repo.

Scans every tracked file (or the paths given) for account-number-shaped digit
runs, dollar amounts outside test fixtures, Google document IDs and URLs, and
common secret formats. A line can opt out with the marker `leak-guard: allow`,
which should be rare and obvious in review.

Binary files (PDFs, images, office files, archives: anything not UTF-8 text)
are refused unless listed in tests/fixtures/BINARIES, the made-up fixtures the
tests need; a listed PDF has its text checked like a fixture's. Pay stubs,
statements and exports are the documents that must never land here, and they
are binary.

Exit status is 1 when anything is found.
"""

import re
import subprocess
import sys
from pathlib import Path

ALLOW_MARKER = "leak-guard: allow"
FIXTURE_DIR = "tests/fixtures/"
SKIP_SUFFIXES = {".lock"}
ALLOWED_BINARIES = FIXTURE_DIR + "BINARIES"  # made-up binary fixtures, one path per line
BINARY_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".heic", ".webp", ".zip", ".gz", ".tgz", ".7z",
                   ".tar", ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ofx", ".qfx"}
BINARY = "binary file not listed in " + ALLOWED_BINARIES

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


def allowed_binaries(read=None):
    """The paths tests/fixtures/BINARIES lists (# starts a comment). read(path) -> text or None."""
    text = read(ALLOWED_BINARIES) if read else (Path(ALLOWED_BINARIES).read_text(encoding="utf-8")
                                                 if Path(ALLOWED_BINARIES).exists() else "")
    return {line.split("#", 1)[0].strip() for line in (text or "").splitlines()} - {""}


def binary(path, data):
    """Whether these bytes are a binary file: a binary suffix, or not UTF-8 text."""
    if Path(path).suffix.lower() in BINARY_SUFFIXES or b"\0" in data[:8192]:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def pdf_text(data):
    """A PDF's text, or None when it can't be read."""
    try:
        import io

        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    except Exception:  # an unreadable PDF is refused, not passed
        return None


def binary_findings(path, data, allowed):
    """(line, check, text) findings for a binary file: refused unless allowed; a listed PDF's text is checked."""
    path = path.replace("\\", "/")
    if path not in allowed:
        return [(0, BINARY, path)]
    if path.lower().endswith(".pdf"):
        text = pdf_text(data)
        if text is None:
            return [(0, "listed PDF that can't be read", path)]
        return _lines(text, True)
    return []


def _lines(text, in_fixtures):
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


def scan(path):
    """Return (line number, check name, line) for each finding in one file."""
    if Path(path).suffix.lower() in SKIP_SUFFIXES or path.endswith("leak_guard.py"):
        return []
    try:
        data = Path(path).read_bytes()
    except (FileNotFoundError, IsADirectoryError):
        return []
    if binary(path, data):
        return binary_findings(path, data, allowed_binaries())
    return _lines(data.decode("utf-8"), path.replace("\\", "/").startswith(FIXTURE_DIR))


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
