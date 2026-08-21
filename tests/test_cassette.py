"""Recorded runs: what makes two runs the same run, and what replay restores."""

import json
import subprocess

import pytest

from skillcheck import cassette
from skillcheck.harnesses import FAKE_STATE, ClaudeHarness, RunResult, merge_turns

STDOUT = "\n".join(
    json.dumps(event)
    for event in [
        {"type": "system", "subtype": "init", "session_id": "s-1", "model": "claude-opus-5"},
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]
            },
        },
        {
            "type": "result",
            "result": "Buried it.",
            "total_cost_usd": 0.02,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
    ]
)


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "workspace"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "existing.txt").write_text("before\n")
    return path


@pytest.fixture
def skill(tmp_path):
    directory = tmp_path / "skills" / "git-graveyard"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: git-graveyard\n---\nBury it.\n")
    return directory


def run_for(workspace, **overrides) -> RunResult:
    harness = ClaudeHarness()
    output, tools, events = harness.parse(STDOUT)
    cost, read, written = harness.usage(events)
    defaults = dict(
        harness="claude",
        prompt="Bury ./deadproj",
        workspace=workspace,
        exit_code=0,
        duration_s=3.0,
        output=output,
        tool_uses=tools,
        stdout=STDOUT,
        stderr="",
        events=events,
        model="claude-opus-5",
        cost_usd=cost,
        input_tokens=read,
        output_tokens=written,
    )
    return RunResult(**{**defaults, **overrides})


# -- what makes a run the same run -----------------------------------------


def test_the_same_inputs_key_to_the_same_recording(skill):
    harness = ClaudeHarness()
    first = cassette.key(harness, "Bury it", [skill], {"a.txt": "x"}, ["yes"], None)
    second = cassette.key(harness, "Bury it", [skill], {"a.txt": "x"}, ["yes"], None)

    assert first == second


def test_editing_the_skill_changes_the_key_so_stale_runs_are_not_replayed(skill):
    harness = ClaudeHarness()
    before = cassette.key(harness, "Bury it", [skill], None, None, None)
    (skill / "SKILL.md").write_text("---\nname: git-graveyard\n---\nBury it, but ask first.\n")
    after = cassette.key(harness, "Bury it", [skill], None, None, None)

    assert before != after


def test_the_prompt_the_model_and_the_answers_all_change_the_key(skill):
    harness = ClaudeHarness()
    base = cassette.key(harness, "Bury it", [skill], None, None, None)

    assert cassette.key(harness, "Bury something else", [skill], None, None, None) != base
    assert cassette.key(harness, "Bury it", [skill], None, ["no"], None) != base
    assert cassette.key(ClaudeHarness(model="haiku"), "Bury it", [skill], None, None, None) != base


def test_a_run_answered_by_a_simulated_user_is_never_recorded(skill):
    assert cassette.key(ClaudeHarness(), "Bury it", [skill], None, lambda q: "yes", None) is None


# -- saving and replaying ---------------------------------------------------


def test_replaying_rebuilds_the_run_from_what_the_cli_printed(workspace, tmp_path):
    tape = tmp_path / "tape.json"
    cassette.save(tape, run_for(workspace, before={"existing.txt": "hash"}))

    played = cassette.load(tape, ClaudeHarness(), workspace, skill="git-graveyard")

    assert played.output == "Buried it."
    assert played.ran("ls")
    assert played.cost_usd == 0.02
    assert played.tokens == 120
    assert played.skill == "git-graveyard"


def test_replaying_puts_the_workspace_back_the_way_the_run_left_it(workspace, tmp_path):
    (workspace / "report.md").write_text("the report\n")
    tape = tmp_path / "tape.json"
    cassette.save(tape, run_for(workspace))

    (workspace / "report.md").unlink()
    cassette.load(tape, ClaudeHarness(), workspace)

    assert (workspace / "report.md").read_text() == "the report\n"


def test_a_file_the_run_deleted_stays_deleted_on_replay(workspace, tmp_path):
    (workspace / "existing.txt").unlink()
    tape = tmp_path / "tape.json"
    cassette.save(tape, run_for(workspace))

    (workspace / "existing.txt").write_text("put back by the test setup\n")
    cassette.load(tape, ClaudeHarness(), workspace)

    assert not (workspace / "existing.txt").exists()


def test_what_a_faked_binary_was_called_with_survives_replay(workspace, tmp_path):
    state = workspace / FAKE_STATE / "gh"
    state.mkdir(parents=True)
    (state / "calls.jsonl").write_text(
        json.dumps({"argv": ["repo", "view", "me/proj"], "status": "ok"}) + "\n"
    )
    tape = tmp_path / "tape.json"
    cassette.save(tape, run_for(workspace))

    (state / "calls.jsonl").write_text("")
    played = cassette.load(tape, ClaudeHarness(), workspace)
    played.fakes = ["gh"]

    assert played.called("gh", "repo", "view", "me/proj")


def test_a_multi_turn_conversation_replays_as_a_multi_turn_run(workspace, tmp_path):
    turns = [run_for(workspace, prompt="Bury it"), run_for(workspace, prompt="yes, go ahead")]
    tape = tmp_path / "tape.json"
    cassette.save(tape, merge_turns(turns))

    played = cassette.load(tape, ClaudeHarness(), workspace)

    assert played.turns == 2
    assert played.prompt == "Bury it"
    assert played.handbacks == ["Buried it."]


def test_a_binary_file_survives_the_round_trip(workspace, tmp_path):
    (workspace / "logo.png").write_bytes(b"\x89PNG\x00\xff")
    tape = tmp_path / "tape.json"
    cassette.save(tape, run_for(workspace))

    (workspace / "logo.png").unlink()
    cassette.load(tape, ClaudeHarness(), workspace)

    assert (workspace / "logo.png").read_bytes() == b"\x89PNG\x00\xff"


def test_asking_for_a_recording_that_was_never_made_returns_nothing(workspace, tmp_path):
    assert cassette.load(tmp_path / "missing.json", ClaudeHarness(), workspace) is None


def test_cleaning_removes_every_recording(tmp_path, workspace):
    tape = cassette.path_for(tmp_path, "claude", "abc")
    cassette.save(tape, run_for(workspace))

    assert cassette.clean(tmp_path) == 1
    assert not tape.exists()
    assert cassette.clean(tmp_path) == 0
