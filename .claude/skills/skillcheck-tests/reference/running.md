# Running tests

Harnesses, settings, flags, and what a run records.

## Harnesses

| Harness | CLI | Skills installed into | Subagents | Isolated |
|---------|-----|----------------------|-----------|----------|
| `claude` | `claude` | `.claude/skills/` | `.claude/agents/` | yes |
| `codex` | `codex` | `.agents/skills/` | `.agents/agents/` | yes |
| `opencode` | `opencode` | `.agents/skills/`, `.opencode/skills/` | `.opencode/agent/` | no |
| `droid` | `droid` | `.factory/skills/` | — | no |

By default skillcheck uses the first of `claude`, `codex`, `opencode`, `droid` that is
installed. A harness whose CLI is missing skips rather than fails, and a test can
restrict itself:

```python
@pytest.mark.harness("claude", "opencode")
def test_something(run_skill): ...
```

**Isolation.** An agent reads globally installed skills from the user's home
directory, and those shadow the copy under test — without isolation the run grades
whatever was last deployed. claude and codex are pointed at a throwaway config
directory with credentials symlinked in. opencode is not: its auth spans several
directories with no single file to link. droid is not either: it reads
`FACTORY_HOME_OVERRIDE`, but its credentials do not survive being moved there.
Assert `not result.reached_home()` to catch a run that escaped, or run the CLI in
a container to prevent it: `--container=<image>`, or `SKILLCHECK_CONTAINER`.

## Settings

Environment first, `[tool.skillcheck]` in `pyproject.toml` second, defaults last.

| Variable | Default | Purpose |
|----------|---------|---------|
| `SKILLCHECK_HARNESS` | first installed | `claude`, `claude,codex`, or `all` |
| `SKILLCHECK_MODEL` | the CLI's own | model for every harness |
| `SKILLCHECK_MODEL_<HARNESS>` | — | per-harness model, e.g. `SKILLCHECK_MODEL_OPENCODE` |
| `SKILLCHECK_JUDGE` | `claude` | `claude`, `anthropic`, or your own callable |
| `SKILLCHECK_JUDGE_MODEL` | `sonnet` | model the judge grades with |
| `SKILLCHECK_TIMEOUT` | `900` | per-turn seconds |
| `SKILLCHECK_RESULTS` | next to the test | where results are written |
| `SKILLCHECK_RECORD` | `1` | set `0` to run without recording |
| `SKILLCHECK_REPLAY` | `off` | `off`, `auto`, `record`, `replay` |
| `SKILLCHECK_CASSETTES` | `.skillcheck/cassettes` | where recorded runs are kept |
| `SKILLCHECK_CONTAINER` | — | image to run the agent CLI inside |

```sh
SKILLCHECK_HARNESS=all ./.claude/skills/my-skill/test.py
```

## pytest flags

skillcheck adds `--harness`, `--skill-model`, `--skill-timeout`, `--keep-workspace`,
`--no-record`, `--replay`, and `--container`. `--keep-workspace` prints the
workspace path and leaves it on disk, which is the fastest way to see what the
agent actually wrote.

Installing skillcheck is enough — it registers as a pytest plugin, extends
`python_files` to collect `test.py`, and switches to importlib import mode so two
skills' `test.py` files do not collide.

## The CLI

```sh
skillcheck test .claude/skills/my-skill     # run a skill's tests
skillcheck lint .claude/skills/             # check skills without running a model
skillcheck status .claude/skills/           # what was tested, and when
skillcheck report .claude/skills/           # pass rate, time, and cost per skill
skillcheck cassettes                        # recorded runs, --clean to drop them
skillcheck doctor                           # installed harnesses and settings
skillcheck new-test .claude/skills/my-skill # scaffold test.py (--force to overwrite)
```

`lint` is the only one that costs nothing and needs no credentials, so it belongs
in CI unconditionally. It reports codes `SK001`–`SK011`: front-matter that is
missing or malformed, a name that disagrees with its directory, a description that
never says when to use the skill, a link to a file that is not there.

## Results

Each run records to `results.json` beside the test file that produced it, so a
skill carries the record of what it was tested on. `skillcheck status` renders it:

```
         claude                     codex
SKILL    STATUS  LAST TESTED  TIME  STATUS  LAST TESTED  TIME
-------  ------  -----------  ----  ------  -----------  ----
justfile pass    today        53s   pass    2 days ago   115s
```

That makes results reviewable: someone running the skill against a harness you do
not have can send the result back as a pull request, and a bug report can point at
the row that failed.

## Recorded runs

A cassette holds what the CLI printed and how the workspace ended up, so the
assertions can run again without a model.

```sh
skillcheck test .claude/skills/ --replay=record   # run for real, keep the recording
skillcheck test .claude/skills/ --replay=replay   # never call a model
skillcheck test .claude/skills/ --replay=auto     # replay when recorded, run when not
```

The key covers the harness, the model, the prompt, the skill's own files, and
whatever the test seeded. Edit the skill and the key changes, so a stale recording
is never replayed against new wording — the test skips instead. A run answered by
a `user=` brief is never recorded.

## Cost

Every live test is at least one model call, and a test using `fake=` spends an
extra one on the verification probe. `samples=n` multiplies by n. Run the tests
for the skill that changed, not the whole tree, and put the free checks first:
`skillcheck lint`, then `--replay=replay` over committed cassettes, then live runs
by hand.
