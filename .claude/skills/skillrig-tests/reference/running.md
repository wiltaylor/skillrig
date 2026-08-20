# Running tests

Harnesses, settings, flags, and what a run records.

## Harnesses

| Harness | CLI | Skills installed into | Subagents | Isolated |
|---------|-----|----------------------|-----------|----------|
| `claude` | `claude` | `.claude/skills/` | `.claude/agents/` | yes |
| `codex` | `codex` | `.agents/skills/` | `.agents/agents/` | yes |
| `opencode` | `opencode` | `.agents/skills/`, `.opencode/skills/` | `.opencode/agent/` | no |

By default skillrig uses the first of `claude`, `codex`, `opencode` that is
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
directories with no single file to link. Assert `not result.reached_home()` to
catch a run that escaped.

## Settings

Environment first, `[tool.skillrig]` in `pyproject.toml` second, defaults last.

| Variable | Default | Purpose |
|----------|---------|---------|
| `SKILLRIG_HARNESS` | first installed | `claude`, `claude,codex`, or `all` |
| `SKILLRIG_MODEL` | the CLI's own | model for every harness |
| `SKILLRIG_MODEL_<HARNESS>` | — | per-harness model, e.g. `SKILLRIG_MODEL_OPENCODE` |
| `SKILLRIG_JUDGE` | `claude` | `claude`, `anthropic`, or your own callable |
| `SKILLRIG_JUDGE_MODEL` | `sonnet` | model the judge grades with |
| `SKILLRIG_TIMEOUT` | `900` | per-turn seconds |
| `SKILLRIG_RESULTS` | next to the test | where results are written |
| `SKILLRIG_RECORD` | `1` | set `0` to run without recording |

```sh
SKILLRIG_HARNESS=all ./.claude/skills/my-skill/test.py
```

## pytest flags

skillrig adds `--harness`, `--skill-model`, `--skill-timeout`, `--keep-workspace`,
and `--no-record`. `--keep-workspace` prints the workspace path and leaves it on
disk, which is the fastest way to see what the agent actually wrote.

Installing skillrig is enough — it registers as a pytest plugin, extends
`python_files` to collect `test.py`, and switches to importlib import mode so two
skills' `test.py` files do not collide.

## The CLI

```sh
skillrig test .claude/skills/my-skill     # run a skill's tests
skillrig status .claude/skills/           # what was tested, and when
skillrig doctor                           # installed harnesses and settings
skillrig new-test .claude/skills/my-skill # scaffold test.py (--force to overwrite)
```

## Results

Each run records to `results.json` beside the test file that produced it, so a
skill carries the record of what it was tested on. `skillrig status` renders it:

```
         claude                     codex
SKILL    STATUS  LAST TESTED  TIME  STATUS  LAST TESTED  TIME
-------  ------  -----------  ----  ------  -----------  ----
justfile pass    today        53s   pass    2 days ago   115s
```

That makes results reviewable: someone running the skill against a harness you do
not have can send the result back as a pull request, and a bug report can point at
the row that failed.

## Cost

Every test is at least one live model call, and a test using `fake=` spends an
extra one on the verification probe. Run the tests for the skill that changed, not
the whole tree. CI cannot run real agents, so live behaviour is checked by hand.
