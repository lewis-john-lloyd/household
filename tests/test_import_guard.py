"""The import guard passes on the package and catches what it is meant to catch."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import import_guard  # noqa: E402


def check_source(tmp_path, source):
    path = tmp_path / "mod.py"
    path.write_text(source)
    return [message for _, message in import_guard.check(path)]


def test_package_is_clean():
    assert import_guard.main(str(Path(__file__).resolve().parents[1] / "src")) == 0


def test_network_and_process_imports_are_rejected(tmp_path):
    found = check_source(tmp_path, "import socket\nfrom urllib import request\nimport subprocess\nimport os\n")
    assert found == ["import of socket", "import from urllib", "import of subprocess", "import of os"]


def test_dynamic_code_is_rejected(tmp_path):
    found = check_source(tmp_path, "eval('1')\n__import__('socket')\nname = 'x'\ngetattr(object, name)\n")
    assert found == ["call to eval", "call to __import__", "getattr with a computed name"]


def test_allowed_and_relative_imports_pass(tmp_path):
    source = "import re\nfrom google.auth import default\nfrom .sheets import read_table\ngetattr(object, 'x', None)\n"
    assert check_source(tmp_path, source) == []
