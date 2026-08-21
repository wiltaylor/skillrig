"""Grading and the simulated user, against a stub backend rather than a model.

The model call itself is not what can be wrong here: the prompt built for it, the
verdict read back out of it, and what gets recorded are.
"""

import pytest

from skillcheck.harnesses import RunResult, merge_turns

# Imported from the modules, not the package: `skillcheck.judge` is the exported
# grading function, which shadows the module of the same name.
from skillcheck.judge import Verdict, ask, compare, judge
from skillcheck.simulate import SENTINEL, User


def result(output="Done.", **overrides) -> RunResult:
    defaults = dict(
        harness="claude",
        prompt="Bury ./deadproj",
        workspace=None,
        exit_code=0,
        duration_s=1.0,
        output=output,
        tool_uses=[],
        stdout="",
        stderr="",
    )
    return RunResult(**{**defaults, **overrides})


# -- grading ---------------------------------------------------------------


def test_a_verdict_reads_as_a_boolean_and_prints_its_reasoning():
    verdict = Verdict(passed=False, score=2, reasoning="it deleted the wrong repo")

    assert not verdict
    assert "FAIL 2/5" in str(verdict)
    assert bool(Verdict(True, 5, "fine"))


def test_the_rubric_and_the_run_both_reach_the_backend():
    seen = {}

    def backend(prompt, model, timeout):
        seen.update(prompt=prompt, model=model, timeout=timeout)
        return {"passed": True, "score": 5, "reasoning": "met every point"}

    verdict = judge("It asks first", "the run", backend=backend, model="haiku", timeout=30)

    assert verdict.passed and verdict.score == 5
    assert "It asks first" in seen["prompt"] and "the run" in seen["prompt"]
    assert seen["model"] == "haiku" and seen["timeout"] == 30


def test_an_unknown_backend_says_what_it_could_have_been():
    with pytest.raises(KeyError, match="unknown judge backend"):
        judge("rubric", "context", backend="gpt")


def test_comparing_two_runs_reports_a_winner():
    def backend(prompt, model, timeout, schema=None):
        assert "run_a" in prompt and "run_b" in prompt
        return {"winner": "b", "reasoning": "b asked before acting"}

    verdict = compare("Asks first", "a said this", "b said that", backend=backend)

    assert verdict.winner == "b"
    assert "asked before" in str(verdict)


def test_comparing_works_with_a_backend_that_takes_no_schema():
    def backend(prompt, model, timeout):
        return {"winner": "tie", "reasoning": "no difference"}

    assert compare("Asks first", "a", "b", backend=backend).winner == "tie"


def test_a_plain_text_call_goes_through_the_text_backends():
    assert ask("say hi", backend=lambda prompt, model, timeout: "hi") == "hi"


# -- the simulated user ----------------------------------------------------


def test_the_user_answers_from_the_brief_and_remembers_the_exchange(monkeypatch):
    prompts = []

    def backend(prompt, model, timeout):
        prompts.append(prompt)
        return "The public graveyard, please."

    user = User("You own deadproj and want it public.", backend=backend)
    reply = user("Which graveyard should this go to?")

    assert reply == "The public graveyard, please."
    assert "You own deadproj" in prompts[0]
    assert "Which graveyard" in prompts[0]
    assert user.heard == ["Which graveyard should this go to?"]
    assert user.said == ["The public graveyard, please."]


def test_the_user_ends_the_conversation_when_the_agent_asks_nothing():
    user = User("brief", backend=lambda prompt, model, timeout: SENTINEL)

    assert user("All done, the repo is buried.") is None
    assert user.said == []


def test_the_second_reply_is_written_knowing_what_was_already_said():
    prompts = []

    def backend(prompt, model, timeout):
        prompts.append(prompt)
        return f"reply {len(prompts)}"

    user = User("brief", backend=backend)
    user("First question?")
    user("Second question?")

    assert "reply 1" in prompts[1]
    assert "First question?" in prompts[1]


# -- what a judge is shown -------------------------------------------------


def test_grading_the_questions_leaves_the_tool_calls_out():
    from skillcheck.harnesses import ToolUse

    run = merge_turns(
        [
            result(
                output="Which graveyard, public or private?",
                tool_uses=[ToolUse("Bash", {"command": "gh repo delete"})],
            ),
            result(output="Buried."),
        ]
    )

    shown = run.transcript(("prompt", "questions", "answer"))

    assert "Which graveyard" in shown
    assert "gh repo delete" not in shown
