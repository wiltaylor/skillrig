"""The command line: what each subcommand prints, and what it exits with."""

import json

import pytest

from skillcheck import cassette, results
from skillcheck.cli import main_cli


@pytest.fixture(autouse=True)
def in_a_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list(dict(__import__("os").environ)):
        if name.startswith("SKILLCHECK_"):
            monkeypatch.delenv(name, raising=False)
    return tmp_path


def write_skill(root, name, description="Use when doing the thing.", with_test=True):
    directory = root / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nDo it.\n"
    )
    if with_test:
        (directory / "test.py").write_text("# tests\n")
    return directory


# -- lint ------------------------------------------------------------------


def test_lint_exits_zero_on_a_clean_tree(tmp_path, capsys):
    write_skill(tmp_path, "demo")

    assert main_cli(["lint", "skills"]) == 0
    assert "0 error(s), 0 warning(s)" in capsys.readouterr().out


def test_lint_exits_one_and_names_the_finding(tmp_path, capsys):
    write_skill(tmp_path, "demo")
    (tmp_path / "skills/demo/SKILL.md").write_text(
        "---\nname: wrong-name\ndescription: Use when.\n---\n"
    )

    assert main_cli(["lint", "skills"]) == 1
    printed = capsys.readouterr().out
    assert "SK005" in printed and "error" in printed


def test_lint_can_be_told_to_ignore_a_finding(tmp_path, capsys):
    write_skill(tmp_path, "demo", description="Does things.", with_test=False)

    assert main_cli(["lint", "skills", "--strict"]) == 1
    assert main_cli(["lint", "skills", "--strict", "--ignore", "SK008", "--ignore", "SK011"]) == 0
    assert "SK008" not in capsys.readouterr().out.splitlines()[-1]


# -- report ----------------------------------------------------------------


def test_report_says_nothing_when_nothing_has_run(capsys):
    assert main_cli(["report"]) == 0
    assert "no results recorded yet" in capsys.readouterr().out


def test_report_shows_the_pass_rate_and_what_it_cost(tmp_path, capsys):
    skill = write_skill(tmp_path, "demo")
    results.merge(
        skill / "results.json",
        "demo",
        {
            "test_a[claude]": {
                "outcome": "passed",
                "harness": "claude",
                "ran_at": results.now(),
                "duration_s": 30.0,
                "cost_usd": 0.25,
                "tokens": 120000,
            },
            "test_b[claude]": {
                "outcome": "failed",
                "harness": "claude",
                "ran_at": results.now(),
                "duration_s": 10.0,
                "cost_usd": 0.15,
                "tokens": 80000,
            },
        },
    )

    assert main_cli(["report"]) == 0

    printed = capsys.readouterr().out
    assert "demo" in printed and "claude" in printed
    assert "1/2" in printed and "50%" in printed
    assert "$0.40" in printed and "200k" in printed


def test_report_leaves_a_skipped_test_out_of_the_rate(tmp_path, capsys):
    skill = write_skill(tmp_path, "demo")
    results.merge(
        skill / "results.json",
        "demo",
        {
            "test_a[claude]": {"outcome": "passed", "harness": "claude", "ran_at": results.now()},
            "test_b[codex]": {"outcome": "skipped", "harness": "codex"},
        },
    )

    main_cli(["report"])
    printed = capsys.readouterr().out

    assert "100%" in printed
    assert "0/0" in printed


# -- cassettes -------------------------------------------------------------


def test_cassettes_lists_what_was_recorded_and_cleans_it(tmp_path, capsys):
    tape = cassette.path_for(tmp_path, "claude", "abc123")
    tape.parent.mkdir(parents=True)
    tape.write_text(json.dumps({"turns": []}))

    assert main_cli(["cassettes"]) == 0
    assert "abc123" in capsys.readouterr().out

    assert main_cli(["cassettes", "--clean"]) == 0
    assert "removed 1" in capsys.readouterr().out
    assert not tape.exists()


# -- doctor and new-test ----------------------------------------------------


def test_doctor_reports_the_recording_mode(monkeypatch, capsys):
    monkeypatch.setenv("SKILLCHECK_REPLAY", "replay")
    monkeypatch.setenv("SKILLCHECK_CONTAINER", "ghcr.io/example/agents:1")

    main_cli(["doctor"])

    printed = capsys.readouterr().out
    assert "recorded runs: replay" in printed
    assert "container: ghcr.io/example/agents:1" in printed


def test_doctor_is_happy_with_no_cli_installed_when_replaying(monkeypatch):
    monkeypatch.setattr("skillcheck.cli.installed", lambda: [])
    monkeypatch.setenv("SKILLCHECK_REPLAY", "replay")

    assert main_cli(["doctor"]) == 0


def test_new_test_scaffolds_a_runnable_file(tmp_path, capsys):
    skill = write_skill(tmp_path, "demo", with_test=False)

    assert main_cli(["new-test", str(skill)]) == 0
    written = (skill / "test.py").read_text()
    assert "run_skill" in written and "from skillcheck import main" in written

    assert main_cli(["new-test", str(skill)]) == 1
    assert main_cli(["new-test", str(skill), "--force"]) == 0


def test_new_test_refuses_a_directory_that_is_not_a_skill(tmp_path, capsys):
    (tmp_path / "notaskill").mkdir()
    assert main_cli(["new-test", str(tmp_path / "notaskill")]) == 1
