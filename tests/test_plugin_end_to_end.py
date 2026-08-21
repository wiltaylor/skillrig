"""The plugin driving a whole test, against a stub CLI instead of a real agent.

The stub prints the same stream-json a coding agent does and writes to the
workspace the way one would, so everything between `run_skill(...)` and
results.json is exercised without a model call or a credential.
"""

import json

import pytest

pytest_plugins = ["pytester"]

# Stands in for the agent CLI. Called as: stub.py <workspace> <prompt> [session]
STUB_CLI = """
import json, os, pathlib, sys

if os.environ.get("STUB_FORBIDDEN"):
    sys.stderr.write("the stub CLI was called when it should not have been\\n")
    raise SystemExit(3)

workspace = pathlib.Path(sys.argv[1])
prompt = sys.argv[2]
resuming = len(sys.argv) > 3

counter = workspace.parent / "calls"
seen = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(seen + 1))

events = [{"type": "system", "subtype": "init", "session_id": "s-1", "model": "stub-1"}]
tools = []

if "ask" in prompt.lower() and not resuming:
    answer = "Which graveyard should this go to, public or private?"
elif "alternate" in prompt.lower() and seen % 2 == 0:
    answer = "Which one did you mean?"
else:
    (workspace / "notes.md").write_text(prompt + "\\n")
    tools.append({"type": "tool_use", "name": "Bash", "input": {"command": "echo done"}})
    answer = "Done."

if "use gh" in prompt.lower():
    import subprocess
    called = subprocess.run(["gh", "repo", "create", "x"], capture_output=True, text=True)
    tools.append({"type": "tool_use", "name": "Bash", "input": {"command": "gh repo create x"}})
    answer = "Tried to create the repo."

if "skillcheck-fake" in prompt:
    tools.append({"type": "tool_use", "name": "Bash", "input": {"command": "gh --skillcheck-fake"}})
    answer = "skillcheck-fake"

events.append({"type": "assistant", "message": {"content": tools}})
events.append(
    {
        "type": "result",
        "result": answer,
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }
)
print("\\n".join(json.dumps(event) for event in events))
"""

CONFTEST = '''
import sys
from pathlib import Path

from skillcheck.harnesses import HARNESSES, ClaudeHarness

STUB = str(Path(__file__).parent / "stub_cli.py")


class StubHarness(ClaudeHarness):
    """A CLI that behaves like claude's, without being one."""

    name = "stub"
    binary = sys.executable
    skill_dirs = (".claude/skills",)
    isolated = False

    def isolate(self, root):
        return {}

    def command(self, prompt, workspace):
        return [sys.executable, STUB, str(workspace), prompt]

    def resume_command(self, prompt, workspace, session):
        return self.command(prompt, workspace) + [session]


HARNESSES["stub"] = StubHarness
'''

SKILL = """\
---
name: demo
description: Write the notes file. Use when the user asks for notes.
---

Write what the user asked into notes.md.
"""


@pytest.fixture
def project(pytester):
    """A tiny repo with one skill in it, ready for a test file."""
    pytester.makefile(".py", stub_cli=STUB_CLI)
    pytester.makeconftest(CONFTEST)
    skill = pytester.path / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL)
    return skill


def write_test(skill, body: str) -> None:
    (skill / "test.py").write_text(body)


def run(pytester, *arguments):
    return pytester.runpytest_subprocess(
        "skills/demo/test.py", "--harness=stub", "-p", "no:randomly", *arguments
    )


def test_a_run_reports_what_the_agent_wrote_and_records_it(pytester, project):
    write_test(
        project,
        """
def test_it_writes_the_notes(run_skill):
    result = run_skill("Write the release notes")

    assert result.exit_code == 0
    assert result.output == "Done."
    assert result.created() == ["notes.md"]
    assert result.read("notes.md").strip() == "Write the release notes"
    assert result.ran("echo done")
    assert result.skill == "demo"
    assert result.cost_usd == 0.01 and result.tokens == 110
""",
    )

    run(pytester, "-q").assert_outcomes(passed=1)

    recorded = json.loads((project / "results.json").read_text())
    row = recorded["runs"]["test_it_writes_the_notes[stub]"]
    assert recorded["skill"] == "demo"
    assert row["outcome"] == "passed"
    assert row["harness"] == "stub" and row["model"] == "stub-1"
    assert row["cost_usd"] == 0.01 and row["tokens"] == 110


def test_the_skill_under_test_is_installed_into_the_workspace(pytester, project):
    write_test(
        project,
        """
def test_the_skill_is_there(run_skill):
    result = run_skill("Write the release notes")
    assert result.exists(".claude/skills/demo/SKILL.md")
""",
    )
    run(pytester, "-q").assert_outcomes(passed=1)


def test_a_mapping_answers_the_question_the_agent_actually_asked(pytester, project):
    write_test(
        project,
        """
def test_it_asks_before_writing(run_skill):
    result = run_skill(
        "ask me which graveyard, then write the notes",
        answers={"which graveyard": "the public one"},
    )

    assert result.turns == 2
    assert result.asked("which graveyard")
    assert result.questions == ["Which graveyard should this go to, public or private?"]
    assert result.turn(0).tool_count("Bash") == 0
    assert result.turn(0).created() == []
    assert result.turn(1).created() == ["notes.md"]
    assert result.output == "Done."
""",
    )
    run(pytester, "-q").assert_outcomes(passed=1)


def test_a_brief_stands_in_for_a_user_and_answers_whatever_is_asked(pytester, project):
    write_test(
        project,
        """
import skillcheck.simulate

def test_the_user_answers(run_skill, monkeypatch):
    # Stands in for the model playing the user: answers a question, and ends the
    # conversation when the agent stops asking.
    def reply(prompt, **kw):
        asked = prompt.split("<agent_message>")[-1]
        return "the public one, please" if "?" in asked else skillcheck.simulate.SENTINEL

    monkeypatch.setattr(skillcheck.simulate, "ask", reply)

    result = run_skill("ask me which graveyard, then write the notes", user="You want it public.")

    assert result.turns == 2
    assert result.output == "Done."
""",
    )
    run(pytester, "-q").assert_outcomes(passed=1)


def test_answers_and_a_simulated_user_together_is_rejected(pytester, project):
    write_test(
        project,
        """
import pytest

def test_both_is_an_error(run_skill):
    with pytest.raises(ValueError, match="not both"):
        run_skill("Write the notes", answers=["yes"], user="You want it public.")
""",
    )
    run(pytester, "-q").assert_outcomes(passed=1)


def test_sampling_reports_how_often_the_behaviour_held(pytester, project):
    write_test(
        project,
        """
def test_it_mostly_writes_the_notes(run_skill):
    runs = run_skill("alternate the reply", samples=4)

    assert len(runs) == 4
    assert runs.rate(lambda run: run.output == "Done.") == 0.5
    assert runs.some(lambda run: run.asked("which one"))
    assert runs.cost_usd == 0.04
""",
    )
    run(pytester, "-q").assert_outcomes(passed=1)


def test_a_verdict_is_recorded_with_the_reasoning_that_produced_it(pytester, project):
    write_test(
        project,
        """
import skillcheck.plugin

def test_it_writes_the_notes(run_skill, judge, monkeypatch):
    monkeypatch.setattr(
        skillcheck.plugin,
        "run_judge",
        lambda rubric, context, **kw: skillcheck.plugin.Verdict(True, 4, "notes were written"),
    )

    result = run_skill("Write the release notes")

    assert judge("The notes were written", result)
""",
    )

    run(pytester, "-q").assert_outcomes(passed=1)

    row = json.loads((project / "results.json").read_text())["runs"][
        "test_it_writes_the_notes[stub]"
    ]
    assert row["judges"] == [
        {
            "kind": "rubric",
            "rubric": "The notes were written",
            "passed": True,
            "score": 4,
            "reasoning": "notes were written",
        }
    ]


def test_grading_the_questions_shows_the_judge_only_those(pytester, project):
    write_test(
        project,
        """
import skillcheck.plugin

def test_it_asks_first(run_skill, judge, monkeypatch):
    shown = {}

    def fake_judge(rubric, context, **kw):
        shown["rubric"], shown["context"] = rubric, context
        return skillcheck.plugin.Verdict(True, 5, "asked first")

    monkeypatch.setattr(skillcheck.plugin, "run_judge", fake_judge)

    result = run_skill(
        "ask me which graveyard, then write the notes",
        answers={"which graveyard": "the public one"},
    )

    assert judge.asked("which graveyard to use", result)
    assert "Which graveyard" in shown["context"]
    assert "echo done" not in shown["context"]
    assert "which graveyard to use" in shown["rubric"]
""",
    )
    run(pytester, "-q").assert_outcomes(passed=1)


def test_two_runs_can_be_compared_against_the_same_rubric(pytester, project):
    write_test(
        project,
        """
import skillcheck.plugin

def test_which_wording_does_better(run_agent, judge, monkeypatch, tmp_path):
    monkeypatch.setattr(
        skillcheck.plugin,
        "run_compare",
        lambda rubric, a, b, **kw: skillcheck.plugin.Comparison("b", "b asked first"),
    )

    first = run_agent("Write the release notes")
    verdict = judge.compare("Asks before writing", first, first)

    assert verdict.winner == "b"
""",
    )
    run(pytester, "-q").assert_outcomes(passed=1)


def test_a_failing_assertion_prints_what_the_agent_did(pytester, project):
    write_test(
        project,
        """
def test_it_does_something_else(run_skill):
    result = run_skill("Write the release notes")
    assert result.output == "something else"
""",
    )

    outcome = run(pytester, "-q")

    outcome.assert_outcomes(failed=1)
    outcome.stdout.fnmatch_lines(["*skillcheck: what the agent did*"])
    outcome.stdout.fnmatch_lines(["*Write the release notes*"])
    outcome.stdout.fnmatch_lines(["*files changed*"])


def test_what_a_fake_refused_is_named_in_the_failure_report(pytester, project):
    write_test(
        project,
        """
def test_it_stays_within_what_the_test_set_up(run_skill):
    result = run_skill("use gh to make a repo", fake={"gh": {"me/proj": {"visibility": "PUBLIC"}}})
    assert result.output == "Done."
""",
    )

    outcome = run(pytester, "-q")

    outcome.assert_outcomes(failed=1)
    outcome.stdout.fnmatch_lines(["*refused by a fake*"])
    outcome.stdout.fnmatch_lines(["*gh: repo create x*"])


def test_a_recorded_run_replays_without_calling_the_cli_again(pytester, project, monkeypatch):
    write_test(
        project,
        """
def test_it_writes_the_notes(run_skill):
    result = run_skill("Write the release notes")
    assert result.read("notes.md").strip() == "Write the release notes"
    assert result.ran("echo done")
""",
    )

    run(pytester, "-q", "--replay=record").assert_outcomes(passed=1)
    tapes = list((pytester.path / ".skillcheck/cassettes").rglob("*.json"))
    assert len(tapes) == 1

    # With the stub refusing to run, only the recording can make this pass.
    monkeypatch.setenv("STUB_FORBIDDEN", "1")
    run(pytester, "-q", "--replay=replay").assert_outcomes(passed=1)


def test_replay_skips_rather_than_silently_calling_a_model(pytester, project):
    write_test(
        project,
        """
def test_never_recorded(run_skill):
    run_skill("Write something that was never recorded")
""",
    )
    run(pytester, "-q", "--replay=replay").assert_outcomes(skipped=1)


def test_editing_the_skill_invalidates_the_recording(pytester, project, monkeypatch):
    write_test(
        project,
        """
def test_it_writes_the_notes(run_skill):
    assert run_skill("Write the release notes").output == "Done."
""",
    )
    run(pytester, "-q", "--replay=record").assert_outcomes(passed=1)

    (project / "SKILL.md").write_text(SKILL + "\nAnd ask first.\n")
    monkeypatch.setenv("STUB_FORBIDDEN", "1")

    run(pytester, "-q", "--replay=replay").assert_outcomes(skipped=1)


def test_a_test_outside_a_skill_directory_says_so(pytester):
    pytester.makefile(".py", stub_cli=STUB_CLI)
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(
        test_loose="""
def test_no_skill_here(run_skill):
    run_skill("Do the thing")
"""
    )

    outcome = pytester.runpytest_subprocess("test_loose.py", "--harness=stub", "-q")

    outcome.assert_outcomes(failed=1)
    outcome.stdout.fnmatch_lines(["*not inside a skill directory*"])


def test_an_unknown_harness_is_refused_before_anything_runs(pytester, project):
    write_test(project, "def test_nothing(run_skill):\n    pass\n")

    outcome = pytester.runpytest_subprocess("skills/demo/test.py", "--harness=nope", "-q")

    assert outcome.ret != 0
    outcome.stdout.fnmatch_lines(["*unknown harness*"])
