"""Settings resolution, collection, and the results file."""

import json

import pytest

from skillrig import results
from skillrig.config import Config
from skillrig.plugin import skill_dir_for

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(dict(__import__("os").environ)):
        if name.startswith("SKILLRIG_"):
            monkeypatch.delenv(name, raising=False)


def test_defaults_to_the_first_installed_harness(monkeypatch, tmp_path):
    monkeypatch.setattr("skillrig.config.installed", lambda names=None: ["codex", "opencode"])
    assert Config.load(tmp_path).harnesses == ["codex"]


def test_environment_selects_one_harness_a_group_or_all(monkeypatch, tmp_path):
    monkeypatch.setenv("SKILLRIG_HARNESS", "codex")
    assert Config.load(tmp_path).harnesses == ["codex"]

    monkeypatch.setenv("SKILLRIG_HARNESS", "claude, opencode")
    assert Config.load(tmp_path).harnesses == ["claude", "opencode"]

    monkeypatch.setenv("SKILLRIG_HARNESS", "all")
    assert Config.load(tmp_path).harnesses == ["claude", "codex", "opencode"]


def test_pyproject_supplies_settings_and_the_environment_overrides_them(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.skillrig]\nharness = 'codex'\ntimeout = 60\n"
        "judge_model = 'haiku'\n[tool.skillrig.models]\nopencode = 'zai/glm-5.3'\n"
    )

    settings = Config.load(tmp_path)
    assert settings.harnesses == ["codex"]
    assert settings.timeout == 60
    assert settings.judge_model == "haiku"
    assert settings.model_for("opencode") == "zai/glm-5.3"
    assert settings.model_for("claude") is None

    monkeypatch.setenv("SKILLRIG_TIMEOUT", "5")
    monkeypatch.setenv("SKILLRIG_MODEL_CLAUDE", "opus")
    monkeypatch.setenv("SKILLRIG_MODEL", "sonnet")
    settings = Config.load(tmp_path)
    assert settings.timeout == 5
    assert settings.model_for("claude") == "opus"
    assert settings.model_for("codex") == "sonnet"


def test_a_test_file_finds_the_skill_it_lives_in(tmp_path):
    skill = tmp_path / "skills" / "my-skill"
    (skill / "test").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: my-skill\n---\n")

    assert skill_dir_for(skill / "test.py") == skill
    assert skill_dir_for(skill / "test" / "test_bury.py") == skill
    assert skill_dir_for(tmp_path / "elsewhere" / "test.py") is None


def test_results_merge_by_test_and_leave_other_rows_alone(tmp_path):
    path = tmp_path / "results.json"
    results.merge(path, "my-skill", {"test_a[claude]": {"outcome": "passed", "harness": "claude"}})
    results.merge(path, "my-skill", {"test_b[codex]": {"outcome": "failed", "harness": "codex"}})

    stored = json.loads(path.read_text())
    assert stored["skill"] == "my-skill"
    assert sorted(stored["runs"]) == ["test_a[claude]", "test_b[codex]"]
    assert stored["runs"]["test_a[claude]"]["outcome"] == "passed"


def test_a_new_record_replaces_the_old_one_rather_than_merging_into_it(tmp_path):
    path = tmp_path / "results.json"
    results.merge(
        path, "s", {"t[claude]": {"outcome": "passed", "judge": {"passed": True, "score": 5}}}
    )
    results.merge(path, "s", {"t[claude]": {"outcome": "failed"}})

    record = json.loads(path.read_text())["runs"]["t[claude]"]
    assert record["outcome"] == "failed"
    assert "judge" not in record, "a failed run kept the previous run's score"


def test_a_skip_never_overwrites_a_real_result(tmp_path):
    path = tmp_path / "results.json"
    results.merge(path, "s", {"t[codex]": {"outcome": "passed", "harness": "codex"}})
    results.merge(path, "s", {"t[codex]": {"outcome": "skipped"}})

    assert json.loads(path.read_text())["runs"]["t[codex]"]["outcome"] == "passed"


def test_status_summarises_one_row_per_skill_and_column_group_per_harness(tmp_path):
    for name, outcome in (("alpha", "passed"), ("beta", "failed")):
        skill = tmp_path / name
        skill.mkdir()
        results.merge(
            skill / "results.json",
            name,
            {
                "t[claude]": {"outcome": outcome, "harness": "claude", "ran_at": results.now(),
                              "duration_s": 12.0},
                "t[codex]": {"outcome": "skipped", "harness": "codex"},
            },
        )

    rows = results.collect([tmp_path])
    harnesses, table = results.summarise(rows)
    assert harnesses == ["claude", "codex"]

    rendered = results.render(harnesses, table)
    assert "alpha" in rendered and "beta" in rendered
    assert "pass" in rendered and "FAIL" in rendered
    assert "n/a" in rendered


def test_a_skill_test_file_is_collected_once_not_twice(pytester):
    """test.py must be collected by the pattern, not by a second hook on top."""
    pytester.makepyfile(
        **{
            "skills/demo/test": "def test_one():\n    assert True\n",
        }
    )
    (pytester.path / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\n---\n")

    def collected(*arguments) -> int:
        outcomes = pytester.runpytest(*arguments, "--collect-only", "-q").parseoutcomes()
        return outcomes.get("test", outcomes.get("tests", 0))

    assert collected() == 1, "collecting the directory found it more than once"
    assert collected("skills/demo/test.py") == 1, "naming the file explicitly collected it twice"
