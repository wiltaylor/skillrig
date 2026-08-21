# Assertions

Everything a test can check about a run, what each member means exactly, and the
four assertions that fail against a skill that behaved correctly.

## The `RunResult`

`run_skill` returns one. Fields first:

| Member | Type | Meaning |
|--------|------|---------|
| `exit_code` | `int` | The agent CLI's exit status. Non-zero means the run itself broke, so assert it first with `result.stderr[-2000:]` as the message |
| `output` | `str` | The agent's final answer |
| `harness` | `str` | Which CLI ran: `claude`, `codex`, `opencode`, `droid` |
| `model` | `str` | The model the harness reported using |
| `workspace` | `Path` | The throwaway git repo the agent worked in |
| `duration_s` | `float` | Wall time |
| `tool_uses` | `list[ToolUse]` | Every tool call across every turn. `ToolUse` has `.name` and `.input` |
| `opening_tool_uses` | `list[ToolUse]` | Tool calls from the first turn only — everything done before the user replied |
| `handbacks` | `list[str]` | What the agent said each time it handed control back and got an answer |
| `turns` | `int` | How many turns the conversation took |
| `skill` | `str` | The skill under test, by directory name |
| `cost_usd` | `float \| None` | What the run cost, where the CLI reports a price. `None` on codex and droid |
| `tokens`, `input_tokens`, `output_tokens` | `int` | Token counts across every turn |
| `before` | `dict[str, str]` | The workspace as it was before the run: path to content hash |
| `stdout`, `stderr`, `events` | | Raw CLI output and parsed events, for debugging a harness |

Methods and properties:

| Call | Returns | Meaning |
|------|---------|---------|
| `exists("path")` | `bool` | A path exists in the workspace |
| `read("path")` | `str` | Its contents |
| `files()` | `list[str]` | Every file in the workspace, harness bookkeeping excluded — **including whatever `files=` seeded** |
| `created()` | `list[str]` | Paths that did not exist before the run |
| `modified()` | `list[str]` | Paths whose contents differ from before the run |
| `deleted()` | `list[str]` | Paths that existed before and are gone |
| `touched()` | `list[str]` | All three, sorted |
| `untouched("src", ...)` | `bool` | Nothing under those paths changed. With no arguments, nothing changed at all |
| `ran(r"regex")` | `bool` | Some `bash`/`shell` call's `command` matched the pattern |
| `ordered(r"a", r"b")` | `bool` | Tool calls matching those patterns happened in that order, other calls allowed between |
| `tool_count("Read")` | `int` | How many times a tool was called, by name |
| `read_files()` | `list[str]` | Workspace-relative paths the agent read, in order, deduplicated |
| `used_skill("name")` | `bool` | A `Skill` tool call mentioned that name |
| `loaded_skill()` | `bool` | The skill was picked up, by tool call **or** by its `SKILL.md` being read. Defaults to the skill under test |
| `used_agent("name")` | `bool` | Delegated to that named subagent. Only meaningful on a harness that names them |
| `delegated()` | `bool` | Delegated to any subagent. Use this where names are not available |
| `questions` | `list[str]` | The hand-backs that look like they put something to the user |
| `all_questions` | `list[str]` | Those, plus the final answer when it asks something |
| `asked_question()` | `bool` | Any hand-back or the final answer looks like a question |
| `asked(r"regex")` | `bool` | One of those questions matched the pattern, case-insensitively |
| `said(r"regex")` | `bool` | Anything the agent said back matched, question or not |
| `acted_before_asking(r"regex")` | `bool` | An **opening-turn** tool call's input matched |
| `reached_home()` | `bool` | Some tool call's input contained the real home directory path |
| `commits()` | `list[Commit]` | Commits in the workspace repo, newest first. Each has `.sha`, `.subject`, `.body` |
| `committed(r"regex")` | `bool` | Some commit message matched |
| `branch()` | `str` | The branch checked out in the workspace |
| `calls("gh")` | `list[list[str]]` | Every argument list a faked binary saw, in order |
| `called("gh", "repo", "view")` | `bool` | A faked binary saw a call starting with those arguments |
| `refusals("gh")` | `list[list[str]]` | Calls the fake refused: commands the test did not anticipate |
| `turn(0)` | `RunResult` | One turn on its own. Turn 0 is everything done before the user replied |
| `transcript(sections)` | `str` | The run as text for a judge. Sections: `prompt`, `questions`, `tools`, `files`, `answer` |

`questions` is approximate on purpose. Agents get no ask-the-user tool in print
mode, so a request arrives as prose in whatever shape the model chose — "Confirm
that I should proceed" is a request with no question mark. The matcher covers `?`,
"confirm", "let me know", "shall I", "should I", "would you like", "tell me
which/what/where", "waiting for you", "say the word", over the last 15 lines. When
a verdict turns on whether the agent really stopped and asked, use
`judge.asked(...)` instead.

## Skills that ask questions

`answers` replies as the agent hands control back, in one of three forms.

**A list** answers each hand-back in order. Answers are sent whenever a turn ends,
not only when the message looks like a question, because gating on punctuation
strands the test. So write each answer to be unambiguous wherever it lands: a bare
"no" reads as "stop" if the agent was still waiting for the go-ahead.

```python
result = run_skill(
    "Merge ./alpha and ./beta into a new monorepo, all local.",
    answers=["Yes, that structure is right. Go ahead.", "No, keep the originals."],
)
```

**A mapping** answers whichever question its pattern matches, so the test survives
the skill reordering what it asks. Each pattern is used once; a question no
pattern matches ends the conversation.

```python
answers={r"which graveyard": "the public one", r"delete.*remote": "no, keep it"}
```

**A brief** hands the conversation to a model playing the user, which answers
whatever the agent actually asks. One small model call per turn, and never
recorded to a cassette.

```python
user="You own deadproj, you want it in the public graveyard, and you are in a hurry."
```

Pass `answers=` or `user=`, never both. `max_turns=` caps a conversation that a
mapping or a brief would otherwise keep alive; a list is finite already.

## Does the skill fire at all?

A description no agent matches is the most common way a skill fails, and it fails
silently. Prompt naturally, without naming the skill, and check both directions:

```python
def test_it_fires_on_its_own(run_skill):
    assert run_skill("This old repo is dead, get it out of my way.").loaded_skill()


def test_it_stays_out_of_unrelated_work(run_skill):
    assert not run_skill("Rename the variable `tmp` in main.py.").loaded_skill()
```

## Runs that are not the same twice

`samples=n` runs the same prompt n times, each in its own workspace, and returns a
`RunSet`: `rate(predicate)`, `every`, `some`, `most`, `first`, `cost_usd`,
`tokens`, and `explain(predicate)` for a line per sample when it fails.

```python
runs = run_skill("Bury ./deadproj", samples=5)
assert runs.rate(lambda run: run.asked("which graveyard")) >= 0.8
```

Use it for a behaviour that matters but wobbles. It multiplies the cost by n, so
one or two tests per skill at most.

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

| Call | Grades |
|------|--------|
| `judge(rubric, result)` | The whole run |
| `judge(rubric, result, scope=("prompt", "questions"))` | Only those sections, so the rubric is not diluted by a wall of tool calls |
| `judge.asked("which graveyard to use", result)` | Whether the agent stopped and asked that, in substance. Use where `asked(pattern)` would pin the wording |
| `judge.files(rubric, result)` | What the run wrote, rather than what it said about it |
| `judge.compare(rubric, a, b)` | Which of two runs did better. Returns `.winner` of `"a"`, `"b"`, or `"tie"` |
| `judge.rate(rubric, runs)` | The fraction of a sample set that passes |

Every verdict lands in `results.json` with its reasoning.

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
holds in a test that seeds anything. Use the diff instead, which is measured
against the workspace as it was after seeding and before the run:

```python
assert result.untouched()                    # the run changed nothing
assert result.untouched("docs/plans")        # it stayed out of there
assert result.created() == ["docs/plans/x/SPEC.md"]
```

**A failing assertion already prints the transcript.** The tool calls, the files
that changed, and anything a fake refused are attached to the failure. Read that
before re-running anything by hand.
