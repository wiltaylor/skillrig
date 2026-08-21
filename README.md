# pytest-skillcheck

[![CI](https://github.com/wiltaylor/pytest-skillcheck/actions/workflows/ci.yml/badge.svg)](https://github.com/wiltaylor/pytest-skillcheck/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pytest-skillcheck)](https://pypi.org/project/pytest-skillcheck/)

![A robot teacher whacking a student robot with a book and pointing at its laptop, which shows an error](docs/skillcheck-teacher.png)

Test agent skills against real coding agents. Run a prompt, assert on what
actually happened, and grade the rest with an LLM.

A skill is a folder of instructions you hand to a coding agent. Nothing checks
that the agent still follows them after you edit the wording. skillcheck runs the
real CLI against a throwaway workspace and tells you.

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest-skillcheck"]
# ///
"""Tests for the git-graveyard skill."""

from skillcheck import main


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

uv fetches pytest-skillcheck, pytest runs, and the skill under test is the one the file
lives in — no `skill=` argument, no `conftest.py`.

## Contents

- [Install](#install)
- [Usage](#usage)
- [Contributing](#contributing)
- [Harnesses](#harnesses)
- [Settings](#settings)
- [Writing tests](#writing-tests)
- [Linting](#linting)
- [Recorded runs](#recorded-runs)
- [Containers](#containers)
- [Results](#results)
- [Commands](#commands)
- [Cost, and what CI can cover](#cost-and-what-ci-can-cover)
- [Adding a harness](#adding-a-harness)
- [Licence](#licence)

## Install

```sh
uv add pytest-skillcheck          # or: pip install pytest-skillcheck
skillcheck doctor          # which agent CLIs are installed, and what they would run
```

skillcheck is a pytest plugin, so its fixtures are available anywhere pytest runs
once it is installed.

## Usage

Scaffold a test beside a skill, then run it:

```sh
skillcheck new-test skills/my-skill    # writes skills/my-skill/test.py
chmod +x skills/my-skill/test.py
./skills/my-skill/test.py
```

The file knows which skill it covers from where it sits, so nothing tells it
twice. Each run appends to `results.json` beside it, and `skillcheck status
skills/` reads those back.

## Contributing

Pull requests are welcome, new harnesses most of all. Run what CI runs before you
open one:

```sh
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest tests -q
```

That suite runs against recorded CLI output and a stub agent, so it needs no
credentials and costs nothing. It cannot run a real agent, so anything you change
in a harness needs a skill test run by hand as well.

## Harnesses

| Harness | CLI | Skills installed into | Subagents | Isolated |
| --- | --- | --- | --- | --- |
| `claude` | `claude` | `.claude/skills/` | `.claude/agents/` | yes |
| `codex` | `codex` | `.agents/skills/` | `.agents/agents/` | yes |
| `opencode` | `opencode` | `.agents/skills/`, `.opencode/skills/` | `.opencode/agent/` | no |
| `droid` | `droid` | `.factory/skills/` | — | no |

By default skillcheck uses the first of `claude`, `codex`, `opencode`, `droid`
that is installed. Override with `SKILLCHECK_HARNESS`:

```sh
SKILLCHECK_HARNESS=codex ./skills/my-skill/test.py
SKILLCHECK_HARNESS=claude,opencode ./skills/my-skill/test.py
SKILLCHECK_HARNESS=all ./skills/my-skill/test.py
```

A test can restrict itself with `@pytest.mark.harness("claude", "opencode")`, and
a harness whose CLI is missing skips rather than fails.

**Isolation.** An agent reads globally installed skills from your home directory,
and those *shadow* the copy under test — so without isolation you would be
grading whatever you last deployed. claude and codex are pointed at a throwaway
config directory with credentials symlinked in. opencode is not: its auth spans
several directories with no single file to link. droid is not either: it takes
`FACTORY_HOME_OVERRIDE`, but its credentials do not survive being moved there.
Assert `not result.reached_home()` to catch this, or run in a
[container](#containers) to prevent it.

## Settings

Environment first, `[tool.skillcheck]` in `pyproject.toml` second, defaults last.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SKILLCHECK_HARNESS` | first installed | `claude`, `claude,codex`, or `all` |
| `SKILLCHECK_MODEL` | the CLI's own | model for every harness |
| `SKILLCHECK_MODEL_<HARNESS>` | — | per-harness model, e.g. `SKILLCHECK_MODEL_OPENCODE` |
| `SKILLCHECK_JUDGE` | `claude` | `claude`, `anthropic`, or your own callable |
| `SKILLCHECK_JUDGE_MODEL` | `sonnet` | model the judge grades with |
| `SKILLCHECK_TIMEOUT` | `900` | per-turn seconds |
| `SKILLCHECK_RESULTS` | next to the test | where results are written |
| `SKILLCHECK_RECORD` | `1` | set `0` to run without recording |
| `SKILLCHECK_REPLAY` | `off` | `off`, `auto`, `record`, `replay` — see [recorded runs](#recorded-runs) |
| `SKILLCHECK_CASSETTES` | `.skillcheck/cassettes` | where recorded runs are kept |
| `SKILLCHECK_CONTAINER` | — | image to run the agent CLI inside |

## Writing tests

### Assertions

`run_skill` returns a `RunResult`.

**What the workspace looks like now.**

```python
result.exists("out.txt")     result.read("out.txt")     result.files()
```

**What the run changed**, against a snapshot taken before the agent started. This
is how a test says *stay out of there*, which "the file exists" cannot.

```python
result.created()             # ["docs/plan.md"]
result.modified()            # paths whose contents differ
result.deleted()             # paths that are gone
result.touched()             # all three, sorted
result.untouched("src")      # nothing under src/ changed
result.untouched()           # nothing at all changed
```

**What the agent did.**

```python
result.ran(r"cargo build")            result.tool_count("Read")
result.used_skill("name")             result.loaded_skill()      # tool call or SKILL.md read
result.used_agent("reviewer")         result.delegated()
result.read_files()                   # workspace-relative paths, in order
result.ordered("repo view", "repo delete")   # these calls, in this order
result.called("gh", "repo", "view")   result.refusals("gh")      result.calls("gh")
result.acted_before_asking(r"--force")       result.reached_home()
```

**What the agent said.**

```python
result.output                result.handbacks           result.questions
result.asked_question()      # it asked something
result.asked("which graveyard")   # it asked about the right thing
result.said(r"dry run")      # anywhere in what it said back, question or not
```

**The git repo it left behind.**

```python
result.commits()             # newest first, each with .sha .subject .body
result.committed(r"^feat:")
result.branch()
```

**What it cost.**

```python
result.cost_usd              # None on a CLI that reports no price
result.tokens                result.input_tokens        result.output_tokens
result.duration_s            result.turns
```

**One turn at a time.** Turn 0 is everything the agent did before the user said
anything, so a match there is work done unasked.

```python
result.turn(0).tool_uses     result.turn(1).created()   result.turn(-1).output
```

When an assertion fails, the transcript, the files that changed, and anything a
fake refused are printed under the failure. You should not have to re-run it by
hand to see what happened.

### Skills that ask questions

`answers` replies as the agent hands control back, in any of three forms.

A **list** answers each hand-back in order. Answers are sent whenever a turn ends,
not only when the message looks like a question: agents ask without question
marks ("Confirm that I should proceed"), and gating on punctuation strands the
test.

```python
result = run_skill("Merge these repos", answers=["Yes, go ahead.", "No, keep the originals."])
```

A **mapping** answers whichever question its pattern matches, so the test survives
the skill reordering what it asks. Each pattern is used once, and a question no
pattern matches ends the conversation.

```python
result = run_skill(
    "Bury ./deadproj",
    answers={r"which graveyard": "the public one", r"delete.*remote": "no, keep it"},
)
```

A **brief** hands the conversation to a model playing the user, which answers
whatever the agent actually asks. Reach for it when you do not want to guess the
wording at all — it costs one small model call per turn.

```python
result = run_skill(
    "Bury ./deadproj",
    user="You own deadproj, you want it in the public graveyard, and you are in a hurry.",
)
```

Then assert on what was asked:

```python
assert result.asked("which graveyard")
assert not result.acted_before_asking(r"--allow-unrelated-histories")
assert len(result.questions) == 1, "the skill asked more than once"
```

`asked` needs the wording to hold still. When it should not have to, judge the
questions instead — see [judging](#judging).

### Does the skill fire at all?

A description that no agent matches is the most common way a skill fails, and it
fails silently. Prompt naturally, without naming the skill, and check it was
picked up:

```python
def test_it_fires_on_its_own(run_skill):
    result = run_skill("This old repo is dead, get it out of my way.")
    assert result.loaded_skill()


def test_it_stays_out_of_unrelated_work(run_skill):
    result = run_skill("Rename the variable `tmp` in main.py.")
    assert not result.loaded_skill()
```

`loaded_skill()` counts both ways a skill is picked up: a tool call on the CLIs
that have one, and reading `SKILL.md` on the CLIs that do not.

### Runs that are not the same twice

Agents are not deterministic, so one green run says less than it looks like.
`samples=` runs the same prompt several times and returns a `RunSet`:

```python
def test_it_usually_asks_first(run_skill):
    runs = run_skill("Bury ./deadproj", samples=5)

    assert runs.rate(lambda run: run.asked("which graveyard")) >= 0.8
    assert runs.every(lambda run: not run.ran(r"repo delete"))
```

`rate`, `every`, `some`, `most`, `first`, `cost_usd`, `tokens`, and
`explain(predicate)` for a line per sample when the assertion fails. Each sample
gets its own workspace.

### Skills that call out to services

`fake` puts a stub binary first on `PATH`, backed by a fixture. The stub answers
what the fixture describes and **refuses everything else loudly**, so a skill
reaching for an unanticipated command fails the test rather than doing something
real.

```python
result = run_skill("Archive it", fake={"gh": {"me/proj": {"visibility": "PRIVATE"}}})

assert not result.refusals("gh")     # nothing unexpected was attempted
```

skillcheck ships purpose-built fakes for `gh` and `curl`. **Any other command is
faked by describing it**, no Python needed: patterns are matched against the
arguments as one string, in the order written.

```python
result = run_skill(
    "Roll out the new image",
    fake={
        "kubectl": {
            "get pods": "NAME   READY\nweb-0  1/1",
            "apply -f .*": {"stdout": "deployment configured", "exit": 0},
            "delete .*": {"stderr": "forbidden", "exit": 1},
        },
        "docker": {"build .*": "built", "push .*": "pushed"},
    },
)

assert result.called("kubectl", "apply", "-f", "deploy.yaml")
```

A response may be a string (its stdout), an object with `stdout`, `stderr`,
`exit`, and `writes`, or a list — each call taking the next entry, for a command
that reports something different once the work has finished. `curl` is keyed by
URL instead, and honours `-o`, `-w '%{http_code}'`, and `-f`:

```python
fake={"curl": {"https://api.example.com/.*": {"body": '{"ok": true}', "status": 200}}}
```

Pass `script=` to `fakebin.install` for a fake of your own.

Four things stand between a test and real infrastructure, and the run only starts
once all of them hold:

1. The stub goes first on `PATH`, and skillcheck runs `gh --skillcheck-fake` itself.
2. The agent then runs the same check in a throwaway session of its own. Checking
   skillcheck's environment proves nothing about the shell the agent's tools run
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

**Show the judge only the part the rubric is about.** A rubric about the questions
graded against a wall of tool calls is a rubric being diluted.

```python
judge("It offered both options", result, scope=("prompt", "questions"))
judge.files("The plan names every repo being merged", result)
```

Sections are `prompt`, `questions`, `tools`, `files`, and `answer`.

**Did it ask, in substance?** `result.asked(pattern)` needs the wording to hold
still. This does not:

```python
verdict = judge.asked("which graveyard the repo should go to", result)
assert verdict, verdict.reasoning
```

**Which wording did better?** Run the same prompt against two versions of a skill
and compare them, for the edit-and-see-what-happens loop:

```python
before = run_agent("Bury ./deadproj", skill="skills/git-graveyard")
after = run_agent("Bury ./deadproj", skill="drafts/git-graveyard")

assert judge.compare("Asks before deleting anything", before, after).winner == "b"
```

**Across samples**, `judge.rate(rubric, runs)` grades each and returns the
fraction that passed.

Every verdict is recorded in `results.json` with the reasoning that produced it,
which is what you want when reviewing a regression six weeks later.

## Linting

The cheapest test in the suite: no model, no credentials, no cost, no flakiness.
It catches a description an agent will never match, a name that disagrees with
its directory, and a link to a reference file that was renamed.

```sh
skillcheck lint skills/            # exits 1 on any error
skillcheck lint skills/ --strict   # warnings count as errors too
skillcheck lint skills/ --ignore SK011
```

| Code | Severity | Checks |
| --- | --- | --- |
| `SK001` | error | the directory has a `SKILL.md` |
| `SK002` | error | it opens with a `---` front-matter block |
| `SK003` | error | front-matter has a `name` |
| `SK004` | error | front-matter has a `description` |
| `SK005` | error | the name agrees with the directory |
| `SK006` | error | the name is lowercase-and-hyphens, within 64 characters |
| `SK007` | error | the description is within 1024 characters |
| `SK008` | warning | the description says *when* to use the skill |
| `SK009` | error | every relative link resolves |
| `SK010` | warning | `SKILL.md` is under 500 lines |
| `SK011` | warning | there is a `test.py` beside it |

This is the part of the suite that belongs in CI unconditionally.

## Recorded runs

A recorded run holds what the CLI printed and what the workspace looked like
afterwards. Replaying rebuilds the `RunResult` from it, so every assertion runs
again for nothing — the model call was the only part that cost anything.

```sh
skillcheck test skills/ --replay=record   # run for real, and keep the recording
skillcheck test skills/ --replay=replay   # never call a model; skip what was not recorded
skillcheck test skills/ --replay=auto     # replay when there is a recording, run when there is not
```

A recording is keyed by everything that could change the answer: the harness, the
model, the prompt, the skill's own files, and whatever the test put in the
workspace. **Edit the skill and the key changes**, so a stale recording is never
replayed against new wording — it skips instead, which is the honest answer.

This is what makes a real suite runnable in CI: record locally, commit the
cassettes, and let CI replay them. Note what it does not check — replay proves
your assertions still hold against the run you recorded, not that today's model
still behaves that way. Re-record when the skill changes, on a schedule, or both.

```sh
skillcheck cassettes            # what has been recorded
skillcheck cassettes --clean    # throw it all away
```

Runs answered by a simulated `user=` brief are never recorded: there is no fixed
conversation to record.

## Containers

`reached_home()` tells you *afterwards* that a run touched the real machine. An
image stops it happening:

```sh
skillcheck test skills/ --container=ghcr.io/example/agents:1
```

The container gets the workspace, its throwaway config directory, and a read-only
home for credentials. The image supplies the agent CLI; skillcheck only decides
what runs inside it.

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
skillcheck status skills/
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
skillcheck test skills/my-skill     # run a skill's tests
skillcheck lint skills/             # check skills without running a model
skillcheck status skills/           # what was tested, and when
skillcheck report skills/           # pass rate, time, and cost per skill
skillcheck cassettes                # recorded runs, and --clean to drop them
skillcheck doctor                   # installed harnesses and settings
skillcheck new-test skills/my-skill # scaffold test.py
```

`report` is the one that decides what a suite is worth running:

```
SKILL          HARNESS  TESTS  PASSED  RATE  TIME  COST   TOKENS  LAST RUN
-------------  -------  -----  ------  ----  ----  -----  ------  --------
git-graveyard  claude   6      5/6     83%   412s  $1.20  840k    today
git-graveyard  droid    6      6/6     100%  380s  -      910k    2 days ago
```

## Cost, and what CI can cover

Every live test is at least one model call. Run the tests for the skill you
changed, not the whole tree, and put the free checks first:

1. `skillcheck lint` — no model, runs on every commit.
2. `--replay=replay` over committed cassettes — no model, re-runs every assertion.
3. Live runs — by hand, on a schedule, or when the skill changes.

skillcheck's own CI covers parsing, the assertion surface, the fakes, the linter,
and the plugin end to end against a stub CLI. It cannot run real agents, so live
behaviour is checked by hand. Agent CLIs change their flags without notice, and
that is where breakage comes from.

## Adding a harness

Subclass `Harness`, implement `command`, `parse`, and ideally `resume_command`,
`session_id`, and `usage`, then register it in `HARNESSES`. Pull requests welcome
— with a recorded sample of the CLI's output in `tests/test_parsing.py`, so the
day it changes its format, that is what breaks first.

## Licence

MIT.
