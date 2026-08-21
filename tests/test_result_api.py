"""What a test can assert about a run, without a model being involved.

Every method here is what a skill author writes in an assertion, so these are the
tests that say what those assertions mean.
"""

import subprocess

import pytest

from skillcheck.harnesses import (
    ClaudeHarness,
    Replies,
    RunResult,
    ToolUse,
    merge_turns,
    snapshot,
)


def result(**overrides) -> RunResult:
    defaults = dict(
        harness="claude",
        prompt="Bury ./deadproj",
        workspace=overrides.pop("workspace", None),
        exit_code=0,
        duration_s=1.0,
        output="Done.",
        tool_uses=[],
        stdout="",
        stderr="",
    )
    return RunResult(**{**defaults, **overrides})


# -- what the agent said ---------------------------------------------------


def test_asked_matches_the_text_of_a_question_not_just_that_one_happened():
    run = merge_turns(
        [
            result(output="Which graveyard should this go to, public or private?"),
            result(output="Buried."),
        ]
    )

    assert run.asked_question()
    assert run.asked("which graveyard")
    assert run.asked(r"public or private")
    assert not run.asked("delete the remote")


def test_asked_covers_a_question_the_agent_ended_on():
    run = result(output="I need the repo name before I continue. Which one?")

    assert run.asked("which one")
    assert run.all_questions == [run.output]


def test_a_done_report_is_not_a_question_to_match_against():
    run = merge_turns([result(output="Merge complete. Nothing else to do."), result(output="ok")])

    assert run.all_questions == []
    assert not run.asked("complete")
    # said() searches everything the agent said, question or not.
    assert run.said("merge complete")


# -- what the agent did ----------------------------------------------------


def test_ordered_holds_only_when_the_calls_happened_in_that_order():
    run = result(
        tool_uses=[
            ToolUse("Bash", {"command": "gh repo view me/proj"}),
            ToolUse("Read", {"file_path": "/w/SKILL.md"}),
            ToolUse("Bash", {"command": "gh repo delete me/proj --yes"}),
        ]
    )

    assert run.ordered("repo view", "repo delete")
    assert run.ordered("repo view", "Read", "repo delete")
    assert not run.ordered("repo delete", "repo view")
    assert not run.ordered("repo view", "never happened")


def test_tool_count_and_read_files_report_what_the_run_spent_its_calls_on():
    run = result(
        workspace=None,
        tool_uses=[
            ToolUse("Read", {"file_path": "docs/a.md"}),
            ToolUse("Read", {"file_path": "docs/a.md"}),
            ToolUse("Read", {"file_path": "docs/b.md"}),
            ToolUse("Bash", {"command": "ls"}),
        ],
    )

    assert run.tool_count("read") == 3
    assert run.tool_count("Bash") == 1
    assert run.read_files() == ["docs/a.md", "docs/b.md"]


def test_read_files_reports_paths_relative_to_the_workspace(tmp_path):
    run = result(
        workspace=tmp_path,
        tool_uses=[ToolUse("Read", {"file_path": f"{tmp_path}/references/deep.md"})],
    )
    assert run.read_files() == ["references/deep.md"]


def test_loaded_skill_counts_a_tool_call_or_the_file_being_read():
    through_tool = result(
        skill="git-graveyard", tool_uses=[ToolUse("Skill", {"skill": "git-graveyard"})]
    )
    through_file = result(
        skill="git-graveyard",
        tool_uses=[ToolUse("Read", {"file_path": "/w/.agents/skills/git-graveyard/SKILL.md"})],
    )
    neither = result(skill="git-graveyard", tool_uses=[ToolUse("Bash", {"command": "ls"})])

    assert through_tool.loaded_skill()
    assert through_file.loaded_skill()
    assert not neither.loaded_skill()
    assert not neither.loaded_skill("some-other-skill")


def test_loaded_skill_says_so_when_there_is_no_skill_to_look_for():
    with pytest.raises(ValueError, match="no skill name"):
        result().loaded_skill()


# -- what the run did to the workspace -------------------------------------


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "workspace"
    (path / "src").mkdir(parents=True)
    (path / "src/keep.py").write_text("original\n")
    (path / "notes.md").write_text("notes\n")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def test_the_diff_says_what_was_created_changed_and_removed(workspace):
    before = snapshot(workspace)
    (workspace / "src/keep.py").write_text("edited\n")
    (workspace / "notes.md").unlink()
    (workspace / "new.txt").write_text("new\n")

    run = result(workspace=workspace, before=before)

    assert run.created() == ["new.txt"]
    assert run.modified() == ["src/keep.py"]
    assert run.deleted() == ["notes.md"]
    assert run.touched() == ["new.txt", "notes.md", "src/keep.py"]


def test_untouched_is_how_a_test_says_stay_out_of_this_directory(workspace):
    before = snapshot(workspace)
    (workspace / "scratch").mkdir()
    (workspace / "scratch/work.txt").write_text("fine\n")

    run = result(workspace=workspace, before=before)

    assert run.untouched("src")
    assert run.untouched("src", "notes.md")
    assert not run.untouched("scratch")
    assert not run.untouched()


def test_a_run_that_changed_nothing_is_untouched_everywhere(workspace):
    run = result(workspace=workspace, before=snapshot(workspace))

    assert run.untouched()
    assert run.touched() == []


def test_the_snapshot_ignores_the_harness_own_directories(workspace):
    (workspace / ".claude/skills/thing").mkdir(parents=True)
    (workspace / ".claude/skills/thing/SKILL.md").write_text("---\n")

    assert ".claude" not in " ".join(snapshot(workspace))


def test_commits_and_branch_report_the_repo_the_agent_left_behind(workspace):
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "feat: add the thing", "-m", "with a body"],
        cwd=workspace,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@e.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@e.invalid",
            "PATH": "/usr/bin:/bin",
        },
    )

    run = result(workspace=workspace)

    assert [commit.subject for commit in run.commits()] == ["feat: add the thing"]
    assert run.committed(r"^feat:")
    assert run.committed("with a body")
    assert not run.committed("chore")
    assert run.branch() in ("main", "master")


def test_a_workspace_with_no_commits_reports_none_rather_than_failing(workspace):
    assert result(workspace=workspace).commits() == []
    assert not result(workspace=workspace).committed("anything")


# -- the transcript a judge is shown ---------------------------------------


def test_the_transcript_can_be_narrowed_to_the_part_a_rubric_is_about(workspace):
    run = merge_turns(
        [
            result(
                workspace=workspace,
                output="Which graveyard, public or private?",
                tool_uses=[ToolUse("Bash", {"command": "gh repo view"})],
            ),
            result(workspace=workspace, output="Buried."),
        ]
    )

    whole = run.transcript()
    assert "gh repo view" in whole and "Which graveyard" in whole

    questions_only = run.transcript(("questions",))
    assert "Which graveyard" in questions_only
    assert "gh repo view" not in questions_only


def test_the_transcript_can_include_what_was_written(workspace):
    before = snapshot(workspace)
    (workspace / "report.md").write_text("the whole report\n")

    text = result(workspace=workspace, before=before).transcript(("files",))
    assert "report.md" in text and "the whole report" in text


def test_an_unknown_transcript_section_is_rejected_rather_than_ignored():
    with pytest.raises(ValueError, match="unknown transcript section"):
        result().transcript(("prmopt",))


# -- turns -----------------------------------------------------------------


def test_each_turn_is_available_on_its_own():
    run = merge_turns(
        [
            result(output="Plan ready. Proceed?", tool_uses=[ToolUse("Read", {"file_path": "a"})]),
            result(output="Done.", tool_uses=[ToolUse("Bash", {"command": "rm -rf x"})]),
        ]
    )

    assert run.turns == 2
    assert run.turn(0).output == "Plan ready. Proceed?"
    assert run.turn(-1).output == "Done."
    assert run.turn(0).tool_count("Bash") == 0
    assert run.turn(1).tool_count("Bash") == 1


def test_what_a_later_turn_wrote_is_not_in_an_earlier_turns_diff(workspace):
    before = snapshot(workspace)
    asked = result(workspace=workspace, output="Which word?", before=before, after=before)

    (workspace / "marker.txt").write_text("banana\n")
    wrote = result(workspace=workspace, output="Done.", before=before, after=snapshot(workspace))
    run = merge_turns([asked, wrote])

    assert run.turn(0).created() == []
    assert run.turn(1).created() == ["marker.txt"]
    # The whole run reads the workspace as it is now, so a test can act and ask again.
    assert run.created() == ["marker.txt"]
    (workspace / "later.txt").write_text("added by the test\n")
    assert run.created() == ["later.txt", "marker.txt"]
    assert run.turn(1).created() == ["marker.txt"]


def test_a_single_turn_run_is_its_own_first_turn():
    run = result(output="Done.")
    assert run.turn(0) is run
    with pytest.raises(IndexError):
        run.turn(1)


def test_merging_turns_adds_up_what_the_run_cost():
    run = merge_turns(
        [
            result(cost_usd=0.02, input_tokens=1000, output_tokens=100),
            result(cost_usd=0.03, input_tokens=1500, output_tokens=200),
        ]
    )

    assert run.cost_usd == pytest.approx(0.05)
    assert run.input_tokens == 2500
    assert run.tokens == 2800


def test_a_harness_that_reports_no_price_leaves_the_cost_unset():
    run = merge_turns([result(), result()])
    assert run.cost_usd is None


# -- how answers are chosen ------------------------------------------------


def test_a_list_of_answers_is_given_in_order_whatever_the_agent_said():
    replies = Replies(["first", "second"])

    assert replies.next("anything at all") == "first"
    assert replies.next("Merge complete.") == "second"
    assert replies.next("and now?") is None


def test_a_mapping_answers_the_question_its_pattern_matches():
    replies = Replies({"which graveyard": "the public one", "delete.*remote": "no, keep it"})

    assert replies.next("Should I delete the remote as well?") == "no, keep it"
    assert replies.next("Which graveyard should I use?") == "the public one"


def test_a_mapping_stops_rather_than_looping_on_the_same_question():
    replies = Replies({"which graveyard": "the public one"})

    assert replies.next("Which graveyard?") == "the public one"
    assert replies.next("Which graveyard?") is None


def test_a_mapping_ignores_a_hand_back_that_asks_nothing():
    replies = Replies({"proceed": "yes"})
    assert replies.next("All done, nothing to proceed with.") is None


def test_a_callable_decides_every_reply_and_when_to_stop():
    said = []

    def user(message):
        said.append(message)
        return "keep going" if len(said) < 2 else None

    replies = Replies(user)
    assert replies.next("Shall I?") == "keep going"
    assert replies.next("And now?") is None


def test_a_list_of_answers_is_never_cut_short_by_the_turn_cap():
    assert Replies(["a", "b", "c"]).limit(2) == 4
    assert Replies({"x": "y"}).limit(8) == 8


def test_answers_of_a_shape_that_cannot_be_used_is_rejected():
    with pytest.raises(TypeError, match="answers must be"):
        Replies("just a string")


# -- running the CLI inside a container ------------------------------------


def test_containerize_mounts_the_workspace_and_keeps_the_same_command(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = ClaudeHarness(container="ghcr.io/example/agents:1")

    argv = harness.containerize(["claude", "-p", "hello"], workspace)

    assert argv[:4] == ["docker", "run", "--rm", "-i"]
    assert f"{tmp_path}:{tmp_path}" in argv
    assert argv[-4:] == ["ghcr.io/example/agents:1", "claude", "-p", "hello"]
    assert "-w" in argv and str(workspace) in argv


def test_without_an_image_the_command_is_left_alone(tmp_path):
    assert ClaudeHarness().container is None
