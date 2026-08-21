#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest-skillcheck"]
# ///
"""Tests for the skillcheck-tests skill. Run it directly: ./.claude/skills/skillcheck-tests/test.py"""

import re

from skillcheck import main

NOTE_TAKER = """\
---
name: note-taker
description: Keep a running log of decisions in notes.md.
---

<overview>
Appends a dated line to notes.md every time the user settles something.
</overview>

<boundaries>
<always>
- Append to notes.md, never rewrite it
</always>
<never>
- Record anything the user has not settled
</never>
</boundaries>
"""

RELEASE_NOTES = """\
---
name: release-notes
description: Draft release notes from merged pull requests.
---

<overview>
Reads merged pull requests with the GitHub CLI and drafts release notes from them.
</overview>

<workflow>
<step order="1">Run `gh pr list --state merged --json title,number` for the repo.</step>
<step order="2">Group the titles and write NOTES.md.</step>
</workflow>

<boundaries>
<never>
- Publish a release. Drafting is the whole job
</never>
</boundaries>
"""


def test_writes_a_runnable_test_beside_the_skill(run_skill):
    result = run_skill(
        "The note-taker skill under .claude/skills has no tests yet. Sort that out.",
        files={".claude/skills/note-taker/SKILL.md": NOTE_TAKER},
    )

    assert result.exit_code == 0, result.stderr[-2000:]
    assert result.exists(".claude/skills/note-taker/test.py"), result.files()

    written = result.read(".claude/skills/note-taker/test.py")
    assert "uv run --script" in written
    assert re.search(r"dependencies\s*=\s*\[[^\]]*skillcheck", written), written
    assert "from skillcheck import main" in written
    assert "main(__file__)" in written
    assert "run_skill" in written

    # The test file's location says which skill it covers, so restating that is
    # the mistake this skill exists to prevent.
    assert "skill=" not in written, written
    assert not [path for path in result.files() if path.endswith("conftest.py")]


def test_fakes_the_cli_a_skill_shells_out_to(run_skill):
    result = run_skill(
        "Add a test for the release-notes skill in .claude/skills/release-notes.",
        files={".claude/skills/release-notes/SKILL.md": RELEASE_NOTES},
    )

    assert result.exit_code == 0, result.stderr[-2000:]
    assert result.exists(".claude/skills/release-notes/test.py"), result.files()

    written = result.read(".claude/skills/release-notes/test.py")
    assert "fake=" in written, written
    assert "gh" in written


if __name__ == "__main__":
    raise SystemExit(main(__file__))
