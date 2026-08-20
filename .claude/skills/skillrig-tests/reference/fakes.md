# Fakes

How a test for a skill that talks to a real service stays away from it.

## `fake=`

`fake` puts a stub binary first on `PATH`, backed by a fixture the test writes.
The stub answers what the fixture describes and refuses everything else loudly, so
a skill reaching for an unanticipated command fails the test rather than doing
something real. Every invocation is logged for the test to assert on.

```python
result = run_skill(
    "Archive ./deadproj.",
    fake={"gh": {"wiltaylor/deadproj": {"visibility": "PUBLIC"}}},
)

assert result.called("gh", "repo", "view", "wiltaylor/deadproj")
assert not result.called("gh", "repo", "delete", "wiltaylor/deadproj")
assert not result.refusals("gh")     # nothing unexpected was attempted
```

`refusals` is the assertion that catches the skill doing something the test never
imagined. Include it in every test that uses a fake.

## The `gh` fixture

skillrig ships one fake, for the GitHub CLI. Its fixture maps `owner/name` to
whatever fields the test wants answered:

```python
fake={"gh": {"wiltaylor/deadproj": {"visibility": "PUBLIC", "contents": ["old-thing"]}}}
```

The stub reads it from `SKILLRIG_FAKE_STATE`, appends every call to `calls.jsonl`
with a status of `ok`, `refused`, or `not-found`, and exits 64 on a refusal.

## Why nothing simpler works

The agent spawns its tools from its own shell in its own process. Patching
Python's `subprocess` cannot reach that, which is why the guard is a real binary
earlier on `PATH`.

Four things stand between a test and real infrastructure, and the run starts only
once all of them hold:

1. The stub goes first on `PATH`, and skillrig runs `<binary> --skillrig-fake`
   itself, checking for the marker.
2. The agent then runs the same check in a throwaway session of its own. Checking
   skillrig's environment proves nothing about the shell the agent's tools run in,
   which can rebuild `PATH` from a profile.
3. `GIT_CONFIG_GLOBAL` is replaced for the run. It rewrites every github.com and
   gitlab.com URL to a path that does not exist, so git cannot reach a forge
   either, and it carries an identity — without one, commits fail and the agent
   starts improvising with the real user's name and email.
4. Anything the fake does not recognise exits non-zero.

A failure at step 1 or 2 aborts before a model runs, while it is still harmless.

## Somewhere harmless to work

A fake covers a service with a CLI. For everything else, give the skill a real
place that does not matter — local bare repos for a git remote, a temp directory
for an output tree — and have the skill read those locations from config rather
than hardcoding them. A skill that hardcodes a real destination cannot be tested
without changing the skill.

## A binary skillrig does not ship a fake for

Write a stub and pass it explicitly:

```python
from skillrig import fakebin

fakebin.install(workspace, "aws", fixture, script=Path("fakes/aws.py"))
```

Model it on `src/skillrig/fakes/gh.py`: answer `--skillrig-fake` with the marker
`skillrig-fake`, read the fixture from `SKILLRIG_FAKE_STATE/<binary>/fixture.json`,
append `{"argv": [...], "status": "..."}` to `calls.jsonl` on every invocation, and
exit non-zero on anything unrecognised. Better still, add it upstream so every
skill gets it.
