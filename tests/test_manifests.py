"""Each Apps Script's manifest against the code it ships: every Google service the code calls is enabled
and its scope declared, nothing broader is asked for, and nothing declared goes unused.

TypeScript compiling, or the exporter's own tests passing, does not show that the deployed script can
call a service: that is the manifest's job, and Apps Script only says so at run time.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCOPE = "https://www.googleapis.com/auth/"
PROJECTS = {  # project -> (manifest, the server code it pushes)
    "web": (ROOT / "web" / "appsscript.json", sorted((ROOT / "web" / "src").glob("*.ts"))),
    "exporter": (ROOT / "exporter" / "appsscript.json", [ROOT / "exporter" / "Code.js"]),
}
# advanced service symbol -> (service id, version, the scopes that allow what this project does with it)
SERVICES = {
    "Sheets": ("sheets", "v4", {"spreadsheets.readonly"}),
    "Drive": ("drive", "v3", {"drive.file"}),
    "Gmail": ("gmail", "v1", {"gmail.readonly"}),
}
TRIGGERS = re.compile(r"\bScriptApp\.(newTrigger|getProjectTriggers|deleteTrigger)\b")
# built-in services that would demand a broad scope (all of Drive, all of Sheets, sending mail, the network)
BROAD_CALLS = re.compile(r"\b(DriveApp|SpreadsheetApp|GmailApp|MailApp|UrlFetchApp|DocumentApp)\.")
BROAD_SCOPES = {"drive", "drive.readonly", "spreadsheets", "gmail.modify", "gmail.compose", "gmail.send",
                "script.external_request"}


def code(paths):
    text = "\n".join(p.read_text() for p in paths)
    text = re.sub(r"/\*[\s\S]*?\*/", " ", text)  # comments may name a service they don't call
    return re.sub(r"(?<![:\"'])//[^\n]*", " ", text)


@pytest.mark.parametrize("project", sorted(PROJECTS))
def test_the_manifest_enables_what_the_code_calls_and_nothing_more(project):
    manifest_path, sources = PROJECTS[project]
    manifest = json.loads(manifest_path.read_text())
    text = code(sources)
    scopes = {s[len(SCOPE):] for s in manifest.get("oauthScopes", [])}
    enabled = {s["userSymbol"]: (s["serviceId"], s["version"])
               for s in manifest.get("dependencies", {}).get("enabledAdvancedServices", [])}
    used = {name for name in SERVICES if re.search(rf"\b{name}\.\w+", text)}
    for name in used:
        service, version, allowed = SERVICES[name]
        assert enabled.get(name) == (service, version), f"{project}: calls {name} but the manifest does not enable it"
        assert scopes & allowed, f"{project}: calls {name} but declares none of {sorted(allowed)}"
    assert set(enabled) <= used, f"{project}: enables {sorted(set(enabled) - used)} but never calls it"
    needed = set().union(*(SERVICES[n][2] for n in used)) | ({"script.scriptapp"} if TRIGGERS.search(text) else set())
    assert scopes <= needed, f"{project}: declares {sorted(scopes - needed)}, which its code does not need"
    assert not scopes & BROAD_SCOPES
    assert not BROAD_CALLS.search(text), f"{project}: {BROAD_CALLS.search(text).group(0)} would need a broad scope"


def test_the_views_build_pushes_this_manifest():
    copy = (ROOT / "web" / "scripts" / "copy.mjs").read_text()
    assert '["appsscript.json", "dist/appsscript.json"]' in copy


def test_the_views_and_the_exporter_each_need_what_they_have():
    # the check above is only as good as its view of the code: these are the calls each makes today
    assert {n for n in SERVICES if re.search(rf"\b{n}\.\w+", code(PROJECTS["web"][1]))} == {"Sheets", "Drive"}
    exporter = code(PROJECTS["exporter"][1])
    assert {n for n in SERVICES if re.search(rf"\b{n}\.\w+", exporter)} == {"Gmail", "Drive", "Sheets"}
    # the exporter reads its searches from Reports and never writes a spreadsheet
    assert set(re.findall(r"\bSheets\.[\w.]+", exporter)) == {"Sheets.Spreadsheets.Values.get"}


@pytest.mark.parametrize("project", ["exporter", "nudge"])
def test_clasp_pushes_only_the_script_and_its_manifest(project):
    # without this, clasp push sends test/*.js too, and Apps Script runs the Node tests (require is not defined)
    ignore = [line for line in (ROOT / project / ".claspignore").read_text().splitlines()
              if line.strip() and not line.startswith("#")]
    assert ignore == ["**/**", "!Code.js", "!appsscript.json"]
    assert sorted(p.name for p in (ROOT / project).iterdir() if p.is_file() and not p.name.startswith(".")) == [
        "Code.js", "appsscript.json"]
