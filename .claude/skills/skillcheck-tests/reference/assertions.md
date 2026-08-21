# Assertions

Everything a test can check about a run, what each member means exactly, and the
four assertions that fail against a skill that behaved correctly.

## The `RunResult`

`run_skill` returns one. Fields first:

| Member | Type | Meaning |
|--------|------|---------|
| `exit_code` | `int` | The agent CLI's exit status. Non-zero means the run itself broke, so assert it first with `result.stderr[-2000:]` as the message |
| `output` | `str` | The agent's final answer |
| `harness` | `str` | Which CLI ran: `claude`, `codex`, `opencode` |
| `model` | `str` | The model the harness reported using |
| `workspace` | `Path` | The throwaway git repo the agent worked in |
| `duration_s` | `float` | Wall time |
| `tool_uses` | `list[ToolUse]` | Every tool call across every turn. `ToolUse` has `.name` and `.input` |
| `opening_tool_uses` | `list[ToolUse]` | Tool calls from the first turn only — everything done before the user replied |
| `handbacks` | `list[str]` | What the agent said each time it handed control back and got an answer |
| `turns` | `int` | How many turns the conversation took |
| `stdout`, `stderr`, `events` | | Raw CLI output and parsed events, for debugging a harness |

Methods and properties:

| Call | Returns | Meaning |
|------|---------|---------|
| `exists("path")` | `bool` | A path exists in the workspace |
| `read("path")` | `str` | Its contents |
| `files()` | `list[str]` | Every file in the workspace, harness bookkeeping excluded — **including whatever `files=` seeded** |
| `ran(r"regex")` | `bool` | Some `bash`/`shell` call's `command` matched the pattern |
| `used_skill("name")` | `bool` | A `Skill` tool call mentioned that name |
| `used_agent("name")` | `bool` | Delegated to that named subagent. Only meaningful on a harness that names them |
| `delegated()` | `bool` | Delegated to any subagent. Use this where names are not available |
| `questions` | `list[str]` | The hand-backs that look like they put something to the user |
| `asked_question()` | `bool` | Any hand-back or the final answer looks like a question |
| `acted_before_asking(r"regex")` | `bool` | An **opening-turn** tool call's input matched |
| `reached_home()` | `bool` | Some tool call's input contained the real home directory path |
| `calls("gh")` | `list[list[str]]` | Every argument list a faked binary saw, in order |
| `called("gh", "repo", "view")` | `bool` | A faked binary saw a call starting with those arguments |
| `refusals("gh")` | `list[list[str]]` | Calls the fake refused: commands the test did not anticipate |
| `transcript()` | `str` | Prompt, questions, tool calls, and final answer as text for a judge |

`questions` is approximate on purpose. Agents get no ask-the-user tool in print
mode, so a request arrives as prose in whatever shape the model chose — "Confirm
that I should proceed" is a request with no question mark. The matcher covers `?`,
"confirm", "let me know", "shall I", "should I", "would you like", "tell me
which/what/where", "waiting for you", "say the word", over the last 15 lines. When
a verdict turns on whether the agent really stopped and asked, judge
`result.handbacks` instead.

## Skills that ask questions

`answers` replies as the agent hands control back. Each entry is the next user
message in the same session.

```python
result = run_skill(
    "Merge ./alpha and ./beta into a new monorepo, all local.",
    answers=["Yes, that structure is right. Go ahead.", "No, keep the originals."],
)
```

Answers are sent whenever a turn ends, not only when the message looks like a
question, because gating on punctuation strands the test. So write each answer to
be unambiguous wherever it lands: a bare "no" reads as "stop" if the agent was
still waiting for the go-ahead.

## Seeding a workspace

`files={"docs/plans/x/MAP.md": TEXT}` writes files into the workspace before the
run, creating parent directories. Use it to give the skill something to act on.
`skill=` and `agent=` install extra skills or subagent definitions — a path, or a
list of them — on top of the skill the test file lives in.

## Judging

```python
def test_explains_what_it_did(run_skill, judge):
    result = run_skill("Do the thing.")

    verdict = judge("The reply names every file it changed and hedges nothing", result)
    assert verdict, verdict.reasoning
```

`judge(rubric, context)` takes a `RunResult` or a string, and returns a `Verdict`
with `.passed`, `.score` (1–5), and `.reasoning`. It is falsy when it fails, so
`assert verdict, verdict.reasoning` prints why.

Write rubrics as requirements, including any exception the skill itself allows: a
rubric that overstates a rule fails compliant work.

## Assertions that fail on correct behaviour

**An answer that restates the rule proves nothing.** An `answers` entry is a
user's reply, not a second copy of the instructions. The moment it repeats the
boundary, the test passes against any skill and the real bug survives. Same for
the prompt: one that spells out the procedure measures obedience, not the skill.

**`acted_before_asking` matches tool input, not intent.** The pattern is tested
against every opening tool call's arguments, so a read-only `Read` of the path you
named matches as readily as a `Write` to it. Against a skill whose job is to
investigate before acting, that assertion fails on success. Check tool names
instead:

```python
def wrote_before_asking(result):
    return any(
        use.name.lower() in ("write", "edit", "notebookedit")
        for use in result.opening_tool_uses
    )
```

Keep `acted_before_asking` for a pattern only a mutating call could produce — a
`git push`, a `--force`, a `--allow-unrelated-histories`.

**`questions` misses a gate phrased as an instruction.** "Tell me to go ahead and
I'll make the edits" is a hand-back, and the matcher may not catch every shape.
Assert on `handbacks`, and on what the hand-back names:

```python
assert result.handbacks, "the skill must hand back for approval"
assert "Q4" in " ".join(result.handbacks)
```

**`files()` includes what `files=` seeded.** So `assert not result.files()` never
holds in a test that seeds anything. Prove a read-only command changed nothing by
comparing content: `assert result.read("docs/plans/x/MAP.md") == SEEDED_MAP`.
