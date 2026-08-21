"""The pytest plugin: fixtures, options, and result recording.

Installing skillcheck is enough — there is no conftest.py to copy. A test file
living inside a skill directory knows which skill it tests, so `run_skill` needs
no skill= argument.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from . import cassette, fakebin, results
from .config import Config
from .harnesses import DEFAULT_MAX_TURNS, HARNESSES, RunResult, get_harness, snapshot
from .judge import Comparison, Verdict
from .judge import compare as run_compare
from .judge import judge as run_judge
from .runset import RunSet
from .simulate import User


def pytest_addoption(parser):
    group = parser.getgroup("skillcheck")
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
    group.addoption(
        "--replay",
        default=None,
        choices=cassette.MODES,
        help="Reuse recorded runs: off, auto, record, or replay (never calls a model)",
    )
    group.addoption(
        "--container",
        default=None,
        help="Run the agent CLI inside this container image instead of on this machine",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "harness(*names): restrict a test to these harnesses")
    # The convention is one test file named for what it is, beside the SKILL.md it
    # covers, which pytest's default `test_*.py` pattern does not match. Extending
    # the pattern rather than collecting the file directly keeps pytest's own
    # handling of explicitly named files, which would otherwise collect it twice
    # and run every test in it twice.
    patterns = config.getini("python_files")
    if "test.py" not in patterns:
        patterns.append("test.py")

    # Every skill names its tests test.py, so the default import mode would see a
    # module basename collision the moment a second skill is collected.
    if config.getoption("importmode") == "prepend":
        config.option.importmode = "importlib"
    config.skillcheck = Config.load(Path(config.rootpath))
    if selected := config.getoption("--harness"):
        config.skillcheck.harnesses = (
            list(HARNESSES) if selected == "all" else [n.strip() for n in selected.split(",")]
        )
    if model := config.getoption("--skill-model"):
        config.skillcheck.models["*"] = model
    if timeout := config.getoption("--skill-timeout"):
        config.skillcheck.timeout = timeout
    if config.getoption("--no-record"):
        config.skillcheck.record = False
    if replay := config.getoption("--replay"):
        config.skillcheck.replay = replay
    if container := config.getoption("--container"):
        config.skillcheck.container = container

    config.skillcheck_records = {}
    config.skillcheck_setup_time = {}


def pytest_generate_tests(metafunc):
    if "harness" in metafunc.fixturenames:
        names = metafunc.config.skillcheck.harnesses
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
            "this test is not inside a skill directory, so skillcheck cannot tell which "
            "skill it covers. Move it next to a SKILL.md, or pass skill= explicitly."
        )
    return found


@pytest.fixture
def harness(request):
    """The agent CLI under test, parametrized from configuration."""
    name = request.param
    settings = request.config.skillcheck
    agent = get_harness(name, model=settings.model_for(name), container=settings.container)
    # Replaying a recorded run never calls the CLI, so it need not be installed.
    if not agent.available() and settings.replay != "replay":
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


def new_workspace(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


@pytest.fixture
def run_agent(harness, workspace, request):
    """Install skills, subagents, and fakes into the workspace, then run a prompt."""
    settings = request.config.skillcheck
    # The agent-level PATH check costs a model call, so it happens once per test
    # rather than once per sample.
    verified: set[str] = set()

    def run(
        prompt: str,
        skill: str | Path | list | None = None,
        agent: str | Path | list | None = None,
        files: dict[str, str] | None = None,
        answers=None,
        fake: dict[str, dict] | None = None,
        timeout: int | None = None,
        samples: int = 1,
        max_turns: int = DEFAULT_MAX_TURNS,
        user: str | None = None,
    ) -> RunResult | RunSet:
        """Run `prompt` against the harness and report what happened.

        `answers` replies to the agent: a list in order, a mapping of pattern to
        reply, or a callable. `user` is a brief for a simulated user, which
        answers whatever the agent actually asks. `samples` runs the same prompt
        more than once and hands back a `RunSet`.
        """
        limit = timeout or settings.timeout
        if user and answers:
            raise ValueError("pass either answers= or user=, not both")
        if user:
            answers = User(user, backend=settings.judge, model=settings.judge_model, timeout=limit)

        default_skill = skill_dir_for(Path(request.node.fspath))
        skills = _paths(skill, default_skill)
        agents = _paths(agent, None)
        under_test = skills[0].name if skills else ""

        def furnish(target: Path) -> None:
            """Everything the run needs in place, without calling a model."""
            for binary, fixture in (fake or {}).items():
                fakebin.install(target, binary, fixture)
                fakebin.verify(harness, target, binary)
            for source in skills:
                harness.install_skill(target, source)
            for source in agents:
                harness.install_agent(target, source)
            for relative, content in (files or {}).items():
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)

        def once(target: Path, index: int) -> RunResult:
            furnish(target)
            identity = None
            if settings.replay != "off":
                identity = cassette.key(harness, prompt, skills, files, answers, fake)
            tape = (
                cassette.path_for(
                    Path(request.config.rootpath),
                    harness.name,
                    f"{identity}-{index}",
                    settings.cassettes,
                )
                if identity
                else None
            )

            if tape and settings.replay in ("auto", "replay"):
                played = cassette.load(tape, harness, target, skill=under_test)
                if played is not None:
                    played.fakes = list(fake or {})
                    return played
                if settings.replay == "replay":
                    pytest.skip(f"no recorded run for this test at {tape}")

            for binary in fake or {}:
                if binary not in verified:
                    fakebin.verify_through_agent(harness, target, binary, limit)
                    verified.add(binary)

            before = snapshot(target)
            result = harness.run(prompt, target, limit, answers=answers, max_turns=max_turns)
            result.before = before
            result.fakes = list(fake or {})
            result.skill = under_test
            if tape and settings.replay in ("auto", "record"):
                cassette.save(tape, result)
            return result

        if samples > 1:
            runs = RunSet(
                once(new_workspace(workspace.parent / f"sample-{index}"), index)
                for index in range(samples)
            )
            remember(request, runs)
            return runs

        result = once(workspace, 0)
        remember(request, [result])
        return result

    return run


def remember(request, runs) -> None:
    """Keep the runs on the test, for the record and for a failure report."""
    kept = getattr(request.node, "skillcheck_runs", [])
    kept.extend(runs)
    request.node.skillcheck_runs = kept

    record = request.config.skillcheck_records.setdefault(request.node.nodeid, {})
    first = runs[0]
    record.update(harness=first.harness, model=first.model)
    costs = [run.cost_usd for run in kept if run.cost_usd is not None]
    if costs:
        record["cost_usd"] = round(sum(costs), 4)
    if tokens := sum(run.tokens for run in kept):
        record["tokens"] = tokens


@pytest.fixture
def run_skill(run_agent, request):
    """`run_agent`, for a test that is about a skill.

    The difference is that this one insists there is a skill to test: a test file
    that has wandered away from its SKILL.md would otherwise run the prompt with
    no skill installed and grade the bare model.
    """

    def run(prompt: str, **options):
        if options.get("skill") is None and skill_dir_for(Path(request.node.fspath)) is None:
            pytest.fail(
                "this test is not inside a skill directory, so skillcheck cannot tell which "
                "skill it covers. Move it next to a SKILL.md, or pass skill= explicitly."
            )
        return run_agent(prompt, **options)

    return run


class Judge:
    """Grade a run against a rubric with a model.

    Callable, so `judge(rubric, result)` still reads as one thing, with the
    narrower questions on top of it: what the agent asked, and which of two runs
    followed the instructions better.
    """

    def __init__(self, request):
        self.request = request
        settings = request.config.skillcheck
        self.backend = settings.judge
        self.model = settings.judge_model
        self.timeout = settings.timeout

    def _text(self, context, scope) -> str:
        if isinstance(context, RunResult):
            return context.transcript(scope) if scope else context.transcript()
        return str(context)

    def _record(self, kind: str, rubric: str, verdict) -> None:
        record = self.request.config.skillcheck_records.setdefault(self.request.node.nodeid, {})
        entry = {"kind": kind, "rubric": " ".join(rubric.split())[:200]}
        if isinstance(verdict, Verdict):
            entry.update(passed=verdict.passed, score=verdict.score, reasoning=verdict.reasoning)
        else:
            entry.update(winner=verdict.winner, reasoning=verdict.reasoning)
        record.setdefault("judges", []).append(entry)

    def __call__(self, rubric: str, context: str | RunResult, scope=None) -> Verdict:
        """Grade `context`, optionally showing the judge only part of the run."""
        verdict = run_judge(
            rubric,
            self._text(context, scope),
            backend=self.backend,
            model=self.model,
            timeout=self.timeout,
        )
        print(f"\njudge: {verdict}")
        self._record("rubric", rubric, verdict)
        return verdict

    def asked(self, expectation: str, result: RunResult) -> Verdict:
        """Grade what the agent put to the user, and nothing else.

        `result.asked(pattern)` needs the wording to hold still. This does not, so
        it is the one to reach for when the question is whether the skill really
        stopped and asked the right thing before acting.
        """
        rubric = (
            "The agent stopped and put a question to the user before doing the work, and "
            f"what it asked amounts to: {expectation.strip()}"
        )
        return self(rubric, result, scope=("prompt", "questions", "answer"))

    def files(self, rubric: str, result: RunResult) -> Verdict:
        """Grade what the run wrote, rather than what it said about it."""
        return self(rubric, result, scope=("prompt", "files"))

    def compare(self, rubric: str, a: RunResult | str, b: RunResult | str) -> Comparison:
        """Say which of two runs better meets the rubric."""
        verdict = run_compare(
            rubric,
            self._text(a, None),
            self._text(b, None),
            backend=self.backend,
            model=self.model,
            timeout=self.timeout,
        )
        print(f"\njudge compare: {verdict}")
        self._record("compare", rubric, verdict)
        return verdict

    def rate(self, rubric: str, runs, scope=None) -> float:
        """The fraction of a sample set that passes the rubric."""
        if not runs:
            return 0.0
        return sum(1 for run in runs if self(rubric, run, scope).passed) / len(runs)


@pytest.fixture
def judge(request):
    """Grade a run against a rubric with a model."""
    return Judge(request)


def failure_report(runs: list[RunResult]) -> str:
    """What the agent actually did, for a failing assertion to be read against.

    Without it a red test says `assert False` and nothing else, and the first
    thing anyone does is run it again by hand to see the transcript.
    """
    blocks = []
    for index, run in enumerate(runs):
        header = f"run {index} | {run.harness} | {run.model} | {run.turns} turn(s)"
        if run.cost_usd:
            header += f" | ${run.cost_usd:.3f}"
        blocks.append(f"== {header}\n{run.transcript()}")

        refused = [
            f"  {binary}: {' '.join(argv)}" for binary in run.fakes for argv in run.refusals(binary)
        ]
        if refused:
            # Nearly always the real cause: the skill reached for a command the
            # test never set up, so the fake refused it and the work never happened.
            blocks.append("== refused by a fake\n" + "\n".join(refused))
        if changed := run.touched():
            blocks.append("== files changed\n" + "\n".join(f"  {path}" for path in changed))
    return "\n\n".join(blocks)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Record how each test ended, so results.json says when it last ran."""
    outcome = yield
    report = outcome.get_result()
    config = item.config

    runs = getattr(item, "skillcheck_runs", [])
    if report.failed and runs:
        report.sections.append(("skillcheck: what the agent did", failure_report(runs)))

    if report.outcome == "skipped":
        config.skillcheck_records.setdefault(report.nodeid, {}).update(
            outcome="skipped", ran_at=results.now()
        )
        return
    if report.when == "setup":
        # Tests that run the agent from a fixture spend their time here.
        config.skillcheck_setup_time[report.nodeid] = report.duration
        return
    if report.when != "call":
        return

    setup = config.skillcheck_setup_time.pop(report.nodeid, 0)
    config.skillcheck_records.setdefault(report.nodeid, {}).update(
        outcome=report.outcome,
        ran_at=results.now(),
        duration_s=round(setup + report.duration, 1),
        file=str(item.fspath),
    )


def pytest_sessionfinish(session):
    """Write each test's record beside the test file that produced it."""
    config = session.config
    if not config.skillcheck.record or config.getoption("--collect-only"):
        return

    by_file: dict[Path, dict[str, dict]] = {}
    for nodeid, record in config.skillcheck_records.items():
        source = record.pop("file", None) or nodeid.split("::")[0]
        by_file.setdefault(Path(source), {})[nodeid.split("::", 1)[-1]] = record

    for source, records in by_file.items():
        skill = skill_dir_for(source)
        if skill is None:
            # A results file describes a skill. Tests that sit outside one -- a
            # project's own suite, with skillcheck merely installed -- have nothing
            # to describe, so they leave nothing behind.
            continue
        results.merge(results.path_for(source, config.skillcheck.results), skill.name, records)
