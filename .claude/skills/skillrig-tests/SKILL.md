---
name: skillrig-tests
description: Write, extend, or repair the test.py that skillrig runs beside a SKILL.md — a real coding agent against the skill in a throwaway workspace, asserted on what it actually did. Covers the RunResult assertion surface, replying with answers= to a skill that asks questions, faking a CLI with fake= so a test never touches real infrastructure, LLM rubrics through judge, harness selection, and results.json. Use when adding a test to a skill, when a skill test goes red and the assertion may be the thing at fault, or when asked how a skill gets tested.
user-invocable: false
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
---

<overview>
skillrig is a pytest plugin that runs a real agent CLI against a skill and reports
what happened. This skill covers writing that test: where the file goes, what to
assert, how to keep a test away from anything real, and how to read a failure. It
produces an executable `test.py` beside the `SKILL.md` it covers, and it stops
short of running it — every test is at least one live model call, so spending them
is the user's call.
</overview>

<variables>
- `${CLAUDE_SKILL_DIR}`: Path to this skill's directory.
- `SKILL_UNDER_TEST`: The directory holding the `SKILL.md` being tested. skillrig
  infers it from where the test file lives, so this decides everything.
- `TEST_FILE`: `SKILL_UNDER_TEST/test.py`. Always that name, always that place.
- `HARNESS`: Which agent CLI runs — `claude`, `codex`, or `opencode`. Unset means
  the first one installed.
</variables>

<workflow>
<step order="1">
Find `SKILL_UNDER_TEST`: the nearest ancestor directory holding a `SKILL.md`.
skillrig walks the same path from the test file, which is why `run_skill` needs no
`skill=` argument and no `conftest.py`. Done when you can name both
`SKILL_UNDER_TEST` and `TEST_FILE` as absolute paths.
</step>

<step order="2">
Read that skill's `<always>` and `<never>` boundaries. They are the claims that
break when someone rewords the skill, so they are what the tests exist to notice.
Turn two or three of them into observables: a file at a path, a command that ran,
a hand-back before any edit. Done when each candidate test names something visible
in the workspace or the transcript afterwards.
</step>

<step order="3">
Write `TEST_FILE` in the canonical shape below, one test per observable. Done when
the file carries the shebang, the `# /// script` block depending on `skillrig`,
`from skillrig import main`, and the `main(__file__)` footer.

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["skillrig"]
# ///
"""Tests for the <name> skill. Run it directly: ./.claude/skills/<name>/test.py"""

from skillrig import main


def test_does_the_thing_it_is_for(run_skill):
    result = run_skill("A prompt a real user would type.")

    assert result.exit_code == 0, result.stderr[-2000:]
    assert result.exists("expected-output.txt")


if __name__ == "__main__":
    raise SystemExit(main(__file__))
```

`run_skill(prompt, skill=, agent=, files=, answers=, fake=, timeout=)` installs the
skill into a fresh git repo, seeds `files=`, and runs the prompt. Its fixtures come
from the installed plugin: `run_skill`, `run_agent`, `judge`, `skill_dir`,
`workspace`, `harness`.
</step>

<step order="4">
Choose the assertions from `${CLAUDE_SKILL_DIR}/reference/assertions.md`. Prefer an
exact assertion over a rubric wherever one exists: where the file landed is exact,
whether the explanation was clear is not. Done when every rubric in the file covers
something no assertion could reach.
</step>

<step order="5">
Where the skill shells out to a service — `gh`, a cloud CLI, anything that acts on
something real — give it a fake per `${CLAUDE_SKILL_DIR}/reference/fakes.md` before
writing the first assertion. Done when no path through the test can reach real
infrastructure, and the test asserts `not result.refusals("<binary>")`.
</step>

<step order="6">
Write prompts and `answers` as the laziest plausible user would send them,
ambiguity included. Ambiguity is the condition a boundary exists for, so a prompt
that spells out the procedure measures the model's obedience rather than the
skill, and an answer that restates the rule passes against any skill at all.

```python
answers=["That's right. Write the TD and nothing else."]   # proves nothing
answers=["Looks right. Write it up."]                      # proves the skill
```
</step>

<step order="7">
Make it executable, then hand the command to the user rather than running it:

```sh
chmod +x <TEST_FILE>
./<TEST_FILE>
```

Done when you have said plainly that the tests have not run and that each one
spends real model calls.
</step>
</workflow>

<reading-a-failure>
A red test is as often a bad assertion as a bad skill. Read the captured `output`
and `tool_uses` in the failure before touching the skill —
`${CLAUDE_SKILL_DIR}/reference/assertions.md` lists the four traps that produce a
confident failure against a skill that did exactly the right thing. An overloaded
API, a timeout, or a model taking a different-but-valid route all show up red too,
so re-run the one test first.
</reading-a-failure>

<reference-files>
| File | Contents | Read when |
|------|----------|-----------|
| `${CLAUDE_SKILL_DIR}/reference/assertions.md` | Every `RunResult` member with its exact semantics, `answers=`, `judge`, and the assertions that fail on correct behaviour | Writing or debugging any assertion |
| `${CLAUDE_SKILL_DIR}/reference/fakes.md` | `fake=`, the `gh` fixture format, the four guards between a test and real infrastructure, adding a fake for a binary skillrig does not ship | The skill calls out to a service |
| `${CLAUDE_SKILL_DIR}/reference/running.md` | Harnesses and isolation, `SKILLRIG_*` settings, `pytest` flags, the `skillrig` CLI, `results.json` | Choosing a harness, changing settings, or reading recorded results |
</reference-files>

<boundaries>
<always>
- Put the test at `SKILL_UNDER_TEST/test.py`, so skillrig infers the skill from its
  location
- Assert against what the skill's own boundaries promise
- Keep the set small: three sharp tests beat ten vague ones, and each one is a
  live model call
- Give a skill that reaches real infrastructure a fake and somewhere harmless to
  work before writing any other part of the test
- Read the captured output in a failure before changing the skill
</always>

<ask>
- Before running a test, since each run spends real model calls
- Which skill to test when the target is ambiguous
- Before overwriting a `test.py` that already exists
</ask>

<never>
- Add a `conftest.py`, or pass `skill=` when the test already lives in the skill —
  both restate what the file's location says
- Write a prompt or an `answers` entry that restates the rule under test
- Use `acted_before_asking` for a path the skill is supposed to read: it matches
  tool input, so a read-only `Read` matches as readily as a `Write`
- Assert `not result.files()` in a test that seeds `files=`, which lists those too
- Let a test reach GitHub, a cloud account, or the user's home directory
</never>
</boundaries>
