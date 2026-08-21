"""Parsing, against output shapes recorded from the real CLIs.

These run without a model call, which is what makes them CI-safe. When a CLI
changes its output, this is where it should break first.
"""

import json

from skillcheck.harnesses import (
    ClaudeHarness,
    CodexHarness,
    OpencodeHarness,
    asks_question,
    merge_turns,
)

CLAUDE = "\n".join(
    json.dumps(event)
    for event in [
        {"type": "system", "subtype": "init", "session_id": "abc-123", "model": "claude-opus-5"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "working"},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "cargo build"}},
                ]
            },
        },
        {"type": "result", "subtype": "success", "result": "Done. Shall I proceed?"},
    ]
)

CODEX = "\n".join(
    json.dumps(event)
    for event in [
        {"type": "thread.started", "thread_id": "01a0-thread"},
        {
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "/bin/bash -lc 'cargo build'"},
        },
        {
            "type": "item.completed",
            "item": {"type": "file_change", "changes": [{"path": "x.txt", "kind": "add"}]},
        },
        {"type": "item.completed", "item": {"type": "collab_tool_call", "tool": "wait"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "All done."}},
    ]
)

OPENCODE = "\n".join(
    json.dumps(event)
    for event in [
        {
            "type": "tool",
            "part": {
                "type": "tool",
                "tool": "task",
                "state": {"input": {"subagent_type": "zebra-keeper", "prompt": "find it"}},
                "sessionID": "ses_123",
            },
        },
        {"type": "text", "part": {"type": "text", "text": "ZEBRA-4417", "sessionID": "ses_123"}},
    ]
)


def test_claude_parses_output_tools_and_session():
    harness = ClaudeHarness()
    output, tools, events = harness.parse(CLAUDE)

    assert output == "Done. Shall I proceed?"
    assert [use.name for use in tools] == ["Bash"]
    assert harness.session_id(events) == "abc-123"
    assert harness.detect_model(events) == "claude-opus-5"


def test_codex_parses_commands_edits_and_delegation():
    harness = CodexHarness()
    output, tools, events = harness.parse(CODEX)

    assert output == "All done."
    assert [use.name for use in tools] == ["Bash", "Edit", "collab"]
    assert harness.session_id(events) == "01a0-thread"


def test_opencode_parses_text_and_task_calls():
    harness = OpencodeHarness()
    output, tools, events = harness.parse(OPENCODE)

    assert output == "ZEBRA-4417"
    assert tools[0].input["subagent_type"] == "zebra-keeper"
    assert harness.session_id(events) == "ses_123"


def test_a_run_that_says_nothing_parses_to_empty_rather_than_crashing():
    for harness in (ClaudeHarness(), CodexHarness()):
        output, tools, events = harness.parse("not json at all\n")
        assert output == ""
        assert tools == []
        assert events == []


def test_asks_question_covers_requests_without_a_question_mark():
    assert asks_question("Ready to go. Shall I proceed?")
    assert asks_question("Confirm that I should proceed with this structure.")
    assert asks_question("Let me know which layout you prefer.")
    assert not asks_question("Merge complete. Nothing else to do.")


def make_result(harness, output, tools=(), exit_code=0):
    from skillcheck.harnesses import RunResult

    return RunResult(
        harness=harness,
        prompt="p",
        workspace=None,
        exit_code=exit_code,
        duration_s=1.0,
        output=output,
        tool_uses=list(tools),
        stdout="",
        stderr="",
    )


def test_merging_turns_keeps_handbacks_and_the_opening_tool_calls():
    from skillcheck.harnesses import ToolUse

    opening = [ToolUse("Bash", {"command": "ls"})]
    first = make_result("claude", "Plan looks like this. Proceed?", opening)
    second = make_result("claude", "Merge complete.", [ToolUse("Bash", {"command": "git merge"})])
    merged = merge_turns([first, second])

    assert merged.turns == 2
    assert merged.handbacks == ["Plan looks like this. Proceed?"]
    assert merged.questions == ["Plan looks like this. Proceed?"]
    assert [use.input["command"] for use in merged.opening_tool_uses] == ["ls"]
    assert merged.acted_before_asking("git merge") is False
    assert merged.acted_before_asking("ls") is True


def test_a_done_report_is_a_handback_but_not_a_question():
    first = make_result("claude", "Merge complete. Nothing else to do.")
    merged = merge_turns([first, make_result("claude", "Understood.")])

    assert merged.handbacks == ["Merge complete. Nothing else to do."]
    assert merged.questions == []


def test_a_failing_turn_makes_the_whole_run_fail():
    merged = merge_turns(
        [make_result("claude", "boom", exit_code=1), make_result("claude", "ok", exit_code=0)]
    )
    assert merged.exit_code == 1
