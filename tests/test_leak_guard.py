import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "leak_guard", Path(__file__).parents[1] / "tools" / "leak_guard.py"
)
leak_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leak_guard)


@pytest.fixture
def findings(tmp_path, monkeypatch):
    """Write text to a path relative to a temp repo root and return the check names found."""
    monkeypatch.chdir(tmp_path)

    def run(text, relpath="src/example.py"):
        path = Path(relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return [name for _, name, _ in leak_guard.scan(relpath)]

    return run


def test_flags_account_number(findings):
    assert findings("acct = '123456789012'\n") == ["account-number-shaped digit run"]


def test_flags_dollar_amount_outside_fixtures(findings):
    assert findings("rent = '$2,150.00'\n") == ["dollar amount"]


def test_allows_dollar_amount_in_fixtures(findings):
    assert findings("date,amount\n2026-01-02,$12.50\n", "tests/fixtures/bank.csv") == []


def test_flags_drive_url_even_in_fixtures(findings):
    text = "see https://drive.google.com/file/d/abc\n"
    assert findings(text, "tests/fixtures/notes.txt") == ["Google Drive URL"]


def test_ignores_dates_and_short_numbers(findings):
    assert findings("when = '2026-10-21'; n = 1500\n") == []


def test_allow_marker(findings):
    assert findings("x = 123456789012  # leak-guard: allow\n") == []
