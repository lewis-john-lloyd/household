"""CI's own supply chain: every action pinned to a commit, so a moved tag can't change what runs, and a token
that can only read."""

import re
from pathlib import Path

WORKFLOWS = sorted((Path(__file__).parent.parent / ".github" / "workflows").glob("*.yml"))


def test_every_action_is_pinned_to_a_commit_and_the_token_only_reads():
    assert WORKFLOWS
    for path in WORKFLOWS:
        text = path.read_text()
        uses = re.findall(r"uses:\s*(\S+)", text)
        assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses), (path.name, uses)
        assert re.search(r"(?m)^permissions:\n\s+contents: read\s*$", text), path.name
