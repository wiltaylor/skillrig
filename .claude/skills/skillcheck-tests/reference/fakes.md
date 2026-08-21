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

skillcheck ships a purpose-built fake for the GitHub CLI. Its fixture maps
`owner/name` to whatever fields the test wants answered:

```python
fake={"gh": {"wiltaylor/deadproj": {"visibility": "PUBLIC", "contents": ["old-thing"]}}}
```

The stub reads it from `SKILLCHECK_FAKE_STATE`, appends every call to `calls.jsonl`
with a status of `ok`, `refused`, or `not-found`, and exits 64 on a refusal.

## The `curl` fixture

Keyed by URL pattern rather than by command. It honours `-o FILE`,
`-w '%{http_code}'`, and `-f`, and it does not mistake a header or a `-d` body for
the URL.

```python
fake={"curl": {
    "https://api.example.com/repos/.*": {"body": '{"name": "proj"}', "status": 200},
    "https://example.com/missing": {"status": 404, "body": "not found"},
}}
```

## Any other binary

Describe the commands and skillcheck supplies the stub — no Python file. Patterns
are regular expressions anchored at the start, matched against the arguments as
one string, tried in the order written.

```python
fake={
    "kubectl": {
        "get pods": "NAME   READY\nweb-0  1/1",
        "apply -f .*": {"stdout": "deployment configured", "exit": 0},
        "delete .*": {"stderr": "forbidden", "exit": 1},
    },
    "terraform": {"commands": {"apply": ["in progress", "in progress", "complete"]}},
}
```

A response is a string (its stdout), an object with `stdout`, `stderr`, `exit`,
and `writes` (paths to write, relative to the workspace), or a list — each call
taking the next entry, the last repeating. Wrap the map in `commands` to use this
engine for a binary that has a purpose-built fake, or to keep other settings
beside it. Anything the fixture does not describe is refused, exit 64.

## Why nothing simpler works

The agent spawns its tools from its own shell in its own process. Patching
Python's `subprocess` cannot reach that, which is why the guard is a real binary
earlier on `PATH`.

Four things stand between a test and real infrastructure, and the run starts only
once all of them hold:

1. The stub goes first on `PATH`, and skillcheck runs `<binary> --skillcheck-fake`
   itself, checking for the marker.
2. The agent then runs the same check in a throwaway session of its own. Checking
   skillcheck's environment proves nothing about the shell the agent's tools run in,
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

## A stub of your own

Reach for this only when the command needs behaviour the fixture cannot describe —
state that changes across calls in a way a list cannot express, or output computed
from the arguments.

```python
from skillcheck import fakebin

fakebin.install(workspace, "aws", fixture, script=Path("fakes/aws.py"))
```

Model it on `src/skillcheck/fakes/gh.py`: answer `--skillcheck-fake` with the marker
`skillcheck-fake`, read the fixture from `SKILLCHECK_FAKE_STATE/<binary>/fixture.json`,
append `{"argv": [...], "status": "..."}` to `calls.jsonl` on every invocation, and
exit non-zero on anything unrecognised. Better still, add it upstream so every
skill gets it.
