# skillrig

Test agent skills against real coding agents. Run a prompt, assert on what
actually happened, and grade the rest with an LLM.

A skill is a folder of instructions you hand to a coding agent. Nothing checks
that the agent still follows them after you edit the wording. skillrig runs the
real CLI against a throwaway workspace and tells you.

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["skillrig"]
# ///
"""Tests for the git-graveyard skill."""

from skillrig import main


def test_buries_a_public_repo_in_the_public_graveyard(run_skill):
    result = run_skill(
        "Bury ./deadproj",
        fake={"gh": {"wiltaylor/deadproj": {"visibility": "PUBLIC"}}},
        answers=["Yes, that graveyard is right. Go ahead."],
    )

    assert result.called("gh", "repo", "view", "wiltaylor/deadproj")
    assert not result.called("gh", "repo", "delete", "wiltaylor/deadproj")


if __name__ == "__main__":
    raise SystemExit(main(__file__))
```

Save that as `skills/git-graveyard/test.py`, `chmod +x`, and run it:

```sh
./skills/git-graveyard/test.py
```

uv fetches skillrig, pytest runs, and the skill under test is the one the file
lives in — no `skill=` argument, no `conftest.py`.

## Install

```sh
uv add skillrig          # or: pip install skillrig
skillrig doctor          # which agent CLIs are installed, and what they would run
```

skillrig is a pytest plugin, so its fixtures are available anywhere pytest runs
once it is installed.

## Harnesses

| Harness | CLI | Skills installed into | Subagents | Isolated |
| --- | --- | --- | --- | --- |
| `claude` | `claude` | `.claude/skills/` | `.claude/agents/` | yes |
| `codex` | `codex` | `.agents/skills/` | `.agents/agents/` | yes |
| `opencode` | `opencode` | `.agents/skills/`, `.opencode/skills/` | `.opencode/agent/` | no |

By default skillrig uses the first of `claude`, `codex`, `opencode` that is
installed. Override with `SKILLRIG_HARNESS`:

```sh
SKILLRIG_HARNESS=codex ./skills/my-skill/test.py
SKILLRIG_HARNESS=claude,opencode ./skills/my-skill/test.py
SKILLRIG_HARNESS=all ./skills/my-skill/test.py
```

A test can restrict itself with `@pytest.mark.harness("claude", "opencode")`, and
a harness whose CLI is missing skips rather than fails.

**Isolation.** An agent reads globally installed skills from your home directory,
and those *shadow* the copy under test — so without isolation you would be
grading whatever you last deployed. claude and codex are pointed at a throwaway
config directory with credentials symlinked in. opencode is not: its auth spans
several directories with no single file to link. Assert `not
result.reached_home()` to catch this.

## Settings

Environment first, `[tool.skillrig]` in `pyproject.toml` second, defaults last.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SKILLRIG_HARNESS` | first installed | `claude`, `claude,codex`, or `all` |
| `SKILLRIG_MODEL` | the CLI's own | model for every harness |
| `SKILLRIG_MODEL_<HARNESS>` | — | per-harness model, e.g. `SKILLRIG_MODEL_OPENCODE` |
| `SKILLRIG_JUDGE` | `claude` | `claude`, `anthropic`, or your own callable |
| `SKILLRIG_JUDGE_MODEL` | `sonnet` | model the judge grades with |
| `SKILLRIG_TIMEOUT` | `900` | per-turn seconds |
| `SKILLRIG_RESULTS` | next to the test | where results are written |
| `SKILLRIG_RECORD` | `1` | set `0` to run without recording |

## Writing tests

### Assertions

`run_skill` returns a `RunResult`:

```python
result.exists("out.txt")          result.read("out.txt")     result.files()
result.ran(r"cargo build")        result.used_skill("name")  result.output
result.used_agent("reviewer")     result.delegated()         result.turns
result.called("gh", "repo", "view")   result.refusals("gh")  result.calls("gh")
result.acted_before_asking(r"--force")   result.reached_home()
```

### Skills that ask questions

`answers` replies as the agent hands control back. Each answer is the next user
message in the same session, and what the agent said at each point is kept in
`result.handbacks`.

```python
result = run_skill("Merge these repos", answers=["Yes, go ahead.", "No, keep the originals."])
```

Answers are sent whenever a turn ends, not only when the message looks like a
question: agents ask without question marks ("Confirm that I should proceed"),
and gating on punctuation strands the test. To check a skill stopped *before* it
acted, assert against the first turn's tool calls, which is exact:

```python
assert not result.acted_before_asking(r"--allow-unrelated-histories")
```

### Skills that call out to services

`fake` puts a stub binary first on `PATH`, backed by a fixture. skillrig ships a
`gh` fake; the stub answers what the fixture describes and **refuses everything
else loudly**, so a skill reaching for an unanticipated command fails the test
rather than doing something real.

```python
result = run_skill("Archive it", fake={"gh": {"me/proj": {"visibility": "PRIVATE"}}})

assert not result.refusals("gh")     # nothing unexpected was attempted
```

Four things stand between a test and real infrastructure, and the run only starts
once all of them hold:

1. The stub goes first on `PATH`, and skillrig runs `gh --skillrig-fake` itself.
2. The agent then runs the same check in a throwaway session of its own. Checking
   skillrig's environment proves nothing about the shell the agent's tools run
   in, which can rebuild `PATH` from a profile.
3. `GIT_CONFIG_GLOBAL` rewrites every github.com and gitlab.com URL to a path
   that does not exist, so git cannot reach a forge either. It also carries an
   identity, because without one commits fail and the agent starts improvising
   with your real name and email.
4. Anything the fake does not recognise exits non-zero.

Nothing patches Python's `subprocess`: the agent spawns tools from its own shell
in its own process, where Python-level mocking cannot reach.

### Judging

Some things no assertion catches — whether the skill explained itself, whether
it asked the right question. Write a rubric:

```python
verdict = judge("The reply states the code and hedges nothing", result)
assert verdict, verdict.reasoning
```

Write rubrics as requirements, including any exception the skill itself allows: a
rubric that overstates a rule fails compliant work.

## Results

Each run records beside the test file that produced it, so a skill carries the
record of what it was tested on:

```
skills/git-graveyard/
├── SKILL.md
├── test.py
└── results.json
```

That makes results reviewable. Someone who runs your skill against a harness you
do not have can send the result back as a pull request, and a bug report can
point at the row that failed.

```sh
skillrig status skills/
```

```
         claude                     codex
SKILL    STATUS  LAST TESTED  TIME  STATUS  LAST TESTED  TIME
-------  ------  -----------  ----  ------  -----------  ----
justfile pass    today        53s   pass    2 days ago   115s
```

A failure is not automatically a regression: an overloaded API, a timeout, or a
model taking a different-but-valid route all show up as red. Re-run the one test
before you change the skill.

## Commands

```sh
skillrig test skills/my-skill     # run a skill's tests
skillrig status skills/           # what was tested, and when
skillrig doctor                   # installed harnesses and settings
skillrig new-test skills/my-skill # scaffold test.py
```

## Cost, and what CI can cover

Every test is at least one live model call. Run the tests for the skill you
changed, not the whole tree. skillrig's own CI covers parsing and the fake-binary
machinery against recorded CLI output; it cannot run real agents, so live
behaviour is checked by hand. Agent CLIs change their flags without notice, and
that is where breakage comes from.

## Adding a harness

Subclass `Harness`, implement `command`, `parse`, and ideally `resume_command`
and `session_id`, then register it in `HARNESSES`. Pull requests welcome.

## Licence

MIT.
