"""Reject commits that could leak household data into this public repo.

Scans every tracked file (or the paths given) for account-number-shaped digit
runs, dollar amounts outside test fixtures, Google document IDs and URLs, and
common secret formats. A line can opt out with the marker `leak-guard: allow`,
which should be rare and obvious in review.

Binary files (PDFs, images, office files, archives: anything not UTF-8 text)
are refused unless listed in tests/fixtures/BINARIES, the made-up fixtures the
tests need. A line there is a path under tests/fixtures/ and the SHA-256 of the
contents approved, so a listed name never vouches for other contents (a new
version needs its own line, or a new hash); a listed PDF has its text checked
like a fixture's. Pay stubs,
statements and exports are the documents that must never land here, and they
are binary.

Exit status is 1 when anything is found.
"""

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ALLOW_MARKER = "leak-guard: allow"
FIXTURE_DIR = "tests/fixtures/"
SKIP_SUFFIXES = {".lock"}
ALLOWED_BINARIES = FIXTURE_DIR + "BINARIES"  # made-up binary fixtures: "path sha256", one per line
BINARY_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".heic",
    ".webp",
    ".zip",
    ".gz",
    ".tgz",
    ".7z",
    ".tar",
    ".xlsx",
    ".xls",
    ".docx",
    ".doc",
    ".pptx",
    ".ofx",
    ".qfx",
}
BINARY = "binary file not listed in " + ALLOWED_BINARIES
CHANGED = "binary file whose contents are not the ones listed (sha256) in " + ALLOWED_BINARIES

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


HASH = re.compile(r"(?<!\w)(?:[0-9a-f]{64}|[0-9a-f]{40})(?!\w)")


def without_hashes(line):
    """The line with whole git and SHA-256 hashes (40 or 64 hex digits) taken out: their digit runs
    are not account numbers."""
    return HASH.sub(" ", line)


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def allowed_binaries(read=None):
    """{path: {sha256}} from tests/fixtures/BINARIES (# starts a comment). A line is a path under
    tests/fixtures/ and the SHA-256 of its approved contents; a line without a hash, or a path
    outside the fixtures, allows nothing. read(path) -> text or None."""
    text = (
        read(ALLOWED_BINARIES)
        if read
        else (Path(ALLOWED_BINARIES).read_text(encoding="utf-8") if Path(ALLOWED_BINARIES).exists() else "")
    )
    out = {}
    for line in (text or "").splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) == 2 and parts[0].startswith(FIXTURE_DIR) and re.fullmatch(r"[0-9a-f]{64}", parts[1]):
            out.setdefault(parts[0], set()).add(parts[1])
    return out


def merge_allowed(*lists):
    """One {path: {sha256}} holding every entry of these."""
    out = {}
    for allowed in lists:
        for path, hashes in allowed.items():
            out.setdefault(path, set()).update(hashes)
    return out


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
    if hashlib.sha256(data).hexdigest() not in allowed[path]:
        return [(0, CHANGED, path)]
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
            if pattern.search(without_hashes(line)):
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
        print(
            f"\nleak guard: possible household data found; remove it, or mark an intended line with '{ALLOW_MARKER}'."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
