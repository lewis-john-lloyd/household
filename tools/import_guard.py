"""Fail if the package could reach anything but Sheets, Drive and local parsing.

The package runs in Colab under the owner's Google sign-in, so what it can import
is what it can do. This walks the syntax tree of every module under src/ and
rejects:
  - imports outside ALLOWED (no sockets, HTTP libraries, subprocesses, os, or
    import machinery), and
  - dynamic code: eval, exec, compile, __import__, and getattr with a computed name.

Widening ALLOWED is a deliberate, reviewable change to this file.
"""

import ast
import sys
from pathlib import Path

ALLOWED = {
    # standard library, pure computation and in-memory I/O
    "csv", "dataclasses", "datetime", "hashlib", "io", "json", "logging", "re", "time", "zipfile",
    # data handling
    "pandas", "pypdf",
    # Google: Sheets, Drive and Colab sign-in
    "gspread", "googleapiclient", "google.auth", "google.colab",
}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "breakpoint"}


def allowed(module):
    return any(module == a or module.startswith(a + ".") for a in ALLOWED)


def check(path):
    """Return (line, message) for each violation in one file."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not allowed(alias.name):
                    found.append((node.lineno, f"import of {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if not allowed(node.module or ""):
                found.append((node.lineno, f"import from {node.module}"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                found.append((node.lineno, f"call to {node.func.id}"))
            elif node.func.id == "getattr" and len(node.args) > 1 and not isinstance(
                    node.args[1], ast.Constant):
                found.append((node.lineno, "getattr with a computed name"))
    return found


def main(root="src"):
    failed = False
    for path in sorted(Path(root).rglob("*.py")):
        for line, message in check(path):
            print(f"{path}:{line}: {message}")
            failed = True
    if failed:
        print("\nimport guard: the package may only use the allowlisted modules in tools/import_guard.py.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
