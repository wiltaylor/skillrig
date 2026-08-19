"""The pytest plugin: fixtures, options, and result recording.

Installing skillrig is enough — there is no conftest.py to copy. A test file
living inside a skill directory knows which skill it tests, so `run_skill` needs
no skill= argument.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from . import fakebin, results
from .config import Config
from .harnesses import HARNESSES, RunResult, get_harness
from .judge import Verdict
from .judge import judge as run_judge


def pytest_addoption(parser):
    group = parser.getgroup("skillrig")
    group.addoption(
        "--harness",
        default=None,
        help=f"Comma-separated harnesses, or 'all'. Options: {sorted(HARNESSES)}",
    )
    group.addoption("--skill-model", default=None, help="Model override for every harness")
    group.addoption("--skill-timeout", type=int, default=None, help="Per-turn timeout in seconds")
    group.addoption(
        "--keep-workspace",
        action="store_true",
        help="Print the workspace path and leave it on disk after the run",
    )
    group.addoption("--no-record", action="store_true", help="Do not update results.json")


def pytest_configure(config):
    config.addinivalue_line("markers", "harness(*names): restrict a test to these harnesses")
    # Every skill names its tests test.py, so the default import mode would see a
    # module basename collision the moment a second skill is collected.
    if config.getoption("importmode") == "prepend":
        config.option.importmode = "importlib"
    config.skillrig = Config.load(Path(config.rootpath))
    if selected := config.getoption("--harness"):
        config.skillrig.harnesses = (
            list(HARNESSES) if selected == "all" else [n.strip() for n in selected.split(",")]
        )
    if model := config.getoption("--skill-model"):
        config.skillrig.models["*"] = model
    if timeout := config.getoption("--skill-timeout"):
        config.skillrig.timeout = timeout
    if config.getoption("--no-record"):
        config.skillrig.record = False

    config.skillrig_records = {}
    config.skillrig_setup_time = {}


def pytest_collect_file(file_path, parent):
    """Collect `skills/<name>/test.py`, which pytest's default pattern misses.

    The convention is one test file named for what it is, sitting beside the
    SKILL.md it covers. `test_*.py` inside a `test/` directory is collected by
    pytest as usual.
    """
    if file_path.name == "test.py":
        return pytest.Module.from_parent(parent, path=file_path)
    return None


def pytest_generate_tests(metafunc):
    if "harness" in metafunc.fixturenames:
        names = metafunc.config.skillrig.harnesses
        for name in names:
            if name not in HARNESSES:
                raise pytest.UsageError(f"unknown harness {name!r}; pick from {sorted(HARNESSES)}")
        metafunc.parametrize("harness", names, indirect=True)


def skill_dir_for(path: Path) -> Path | None:
    """The skill a test file belongs to: nearest ancestor holding a SKILL.md."""
    for directory in [Path(path).parent, *Path(path).parents]:
        if (directory / "SKILL.md").is_file():
            return directory
    return None


@pytest.fixture
def skill_dir(request) -> Path:
    """The skill under test, inferred from where the test file lives."""
    found = skill_dir_for(Path(request.node.fspath))
    if found is None:
        pytest.fail(
            "this test is not inside a skill directory, so skillrig cannot tell which "
            "skill it covers. Move it next to a SKILL.md, or pass skill= explicitly."
        )
    return found


@pytest.fixture
def harness(request):
    """The agent CLI under test, parametrized from configuration."""
    name = request.param
    agent = get_harness(name, model=request.config.skillrig.model_for(name))
    if not agent.available():
        pytest.skip(f"{name} CLI is not installed")
    marker = request.node.get_closest_marker("harness")
    if marker and name not in marker.args:
        pytest.skip(f"this test is not written for the {name} harness")
    return agent


@pytest.fixture
def workspace(tmp_path, request) -> Path:
    """An empty git repo the agent works inside."""
    path = tmp_path / "workspace"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    yield path
    if request.config.getoption("--keep-workspace"):
        print(f"\nworkspace kept: {path}")
    else:
        shutil.rmtree(path, ignore_errors=True)


def _paths(value, default: Path | None) -> list[Path]:
    if value is None:
        return [default] if default else []
    values = [value] if isinstance(value, (str, Path)) else list(value)
    return [Path(item) for item in values]


@pytest.fixture
def run_agent(harness, workspace, request):
    """Install skills, subagents, and fakes into the workspace, then run a prompt."""

    def run(
        prompt: str,
        skill: str | Path | list | None = None,
        agent: str | Path | list | None = None,
        files: dict[str, str] | None = None,
        answers: list[str] | None = None,
        fake: dict[str, dict] | None = None,
        timeout: int | None = None,
    ) -> RunResult:
        limit = timeout or request.config.skillrig.timeout

        for binary, fixture in (fake or {}).items():
            fakebin.install(workspace, binary, fixture)
            fakebin.verify(harness, workspace, binary)
            fakebin.verify_through_agent(harness, workspace, binary, limit)

        default_skill = skill_dir_for(Path(request.node.fspath))
        for source in _paths(skill, default_skill):
            harness.install_skill(workspace, source)
        for source in _paths(agent, None):
            harness.install_agent(workspace, source)
        for relative, content in (files or {}).items():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

        result = harness.run(prompt, workspace, limit, answers=answers)
        record = request.config.skillrig_records.setdefault(request.node.nodeid, {})
        record.update(harness=harness.name, model=result.model)
        return result

    return run


@pytest.fixture
def run_skill(run_agent):
    """`run_agent` under a name that reads better for a skill test."""
    return run_agent


@pytest.fixture
def judge(request):
    """Grade a run against a rubric with a model."""
    settings = request.config.skillrig

    def grade(rubric: str, context: str | RunResult) -> Verdict:
        if isinstance(context, RunResult):
            context = context.transcript()
        verdict = run_judge(
            rubric,
            context,
            backend=settings.judge,
            model=settings.judge_model,
            timeout=settings.timeout,
        )
        print(f"\njudge: {verdict}")
        record = request.config.skillrig_records.setdefault(request.node.nodeid, {})
        record["judge"] = {"passed": verdict.passed, "score": verdict.score}
        return verdict

    return grade


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Record how each test ended, so results.json says when it last ran."""
    outcome = yield
    report = outcome.get_result()
    config = item.config

    if report.outcome == "skipped":
        config.skillrig_records.setdefault(report.nodeid, {}).update(
            outcome="skipped", ran_at=results.now()
        )
        return
    if report.when == "setup":
        # Tests that run the agent from a fixture spend their time here.
        config.skillrig_setup_time[report.nodeid] = report.duration
        return
    if report.when != "call":
        return

    setup = config.skillrig_setup_time.pop(report.nodeid, 0)
    config.skillrig_records.setdefault(report.nodeid, {}).update(
        outcome=report.outcome,
        ran_at=results.now(),
        duration_s=round(setup + report.duration, 1),
        file=str(item.fspath),
    )


def pytest_sessionfinish(session):
    """Write each test's record beside the test file that produced it."""
    config = session.config
    if not config.skillrig.record or config.getoption("--collect-only"):
        return

    by_file: dict[Path, dict[str, dict]] = {}
    for nodeid, record in config.skillrig_records.items():
        source = record.pop("file", None) or nodeid.split("::")[0]
        by_file.setdefault(Path(source), {})[nodeid.split("::", 1)[-1]] = record

    for source, records in by_file.items():
        skill = skill_dir_for(source)
        path = results.path_for(source, config.skillrig.results)
        results.merge(path, skill.name if skill else "", records)
