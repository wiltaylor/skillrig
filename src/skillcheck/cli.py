"""`skillcheck` on the command line, and `main()` for a standalone test file."""

import argparse
import shutil
import sys
from pathlib import Path

import pytest

from . import cassette, lint, results
from .config import HARNESS_ORDER, Config, installed
from .harnesses import HARNESSES

TEMPLATE = '''#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest-skillcheck"]
# ///
"""Tests for the {name} skill. Run it directly: ./{path}"""

from skillcheck import main


def test_{slug}_does_its_job(run_skill):
    result = run_skill("Ask the skill to do the thing it is for.")

    assert result.exit_code == 0, result.stderr[-2000:]
    # assert result.exists("some-file")
    # assert result.ran(r"some --command")


if __name__ == "__main__":
    raise SystemExit(main(__file__))
'''


def main(test_file: str | None = None, argv: list[str] | None = None) -> int:
    """Run pytest over one test file. Used by a skill's standalone test.py."""
    arguments = list(argv if argv is not None else sys.argv[1:])
    target = str(Path(test_file).resolve()) if test_file else "."
    return pytest.main([target, "-v", "-s", *arguments])


def cmd_test(args) -> int:
    return pytest.main([*(args.paths or ["."]), "-v", "-s", *args.pytest_args])


def cmd_status(args) -> int:
    rows = results.collect([Path(path) for path in (args.paths or ["."])])
    if not rows:
        print("no results recorded yet")
        return 0
    harnesses, table = results.summarise(rows)
    print(results.render(harnesses, table))

    counts = {
        "passing": sum(1 for row in rows if row.get("outcome") == "passed"),
        "failing": sum(1 for row in rows if row.get("outcome") == "failed"),
        "not applicable": sum(1 for row in rows if row.get("outcome") == "skipped"),
    }
    print("\n" + ", ".join(f"{count} {label}" for label, count in counts.items()))
    return 0


def cmd_report(args) -> int:
    rows = results.collect([Path(path) for path in (args.paths or ["."])])
    if not rows:
        print("no results recorded yet")
        return 0
    print(results.report(rows))
    return 0


def cmd_lint(args) -> int:
    findings = lint.lint([Path(path) for path in (args.paths or ["."])], strict=args.strict)
    for finding in findings:
        if finding.code not in (args.ignore or []):
            print(finding)

    kept = [finding for finding in findings if finding.code not in (args.ignore or [])]
    errors = sum(1 for finding in kept if finding.severity == lint.ERROR)
    warnings = len(kept) - errors
    print(f"\n{errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def cmd_cassettes(args) -> int:
    settings = Config.load(Path.cwd())
    if args.clean:
        removed = cassette.clean(Path.cwd(), settings.cassettes)
        print(f"removed {removed} recorded run(s)")
        return 0

    directory = Path(settings.cassettes or Path.cwd() / cassette.DIRNAME)
    tapes = sorted(directory.rglob("*.json")) if directory.is_dir() else []
    for tape in tapes:
        print(f"{tape.parent.name}  {tape.stem}")
    print(f"\n{len(tapes)} recorded run(s) in {directory}")
    return 0


def cmd_doctor(args) -> int:
    settings = Config.load(Path.cwd())
    ready = installed()
    print("harnesses")
    for name in HARNESS_ORDER:
        binary = HARNESSES[name].binary
        where = shutil.which(binary)
        model = settings.model_for(name) or HARNESSES[name].default_model or "the CLI default"
        state = f"{where}  model: {model}" if where else "not installed"
        print(f"  {name:<10} {state}")

    print(f"\nwould run against: {', '.join(settings.harnesses) or 'nothing'}")
    print(f"judge: {settings.judge} ({settings.judge_model})")
    print(f"timeout: {settings.timeout}s")
    print(f"recorded runs: {settings.replay}")
    if settings.container:
        print(f"container: {settings.container}")
    if not ready and settings.replay != "replay":
        print("\nno agent CLI found on PATH, so every test would skip")
        return 1
    return 0


def cmd_new(args) -> int:
    skill = Path(args.skill)
    if not (skill / "SKILL.md").is_file():
        print(f"no SKILL.md in {skill}", file=sys.stderr)
        return 1
    target = skill / "test.py"
    if target.exists() and not args.force:
        print(f"{target} already exists; pass --force to overwrite", file=sys.stderr)
        return 1

    slug = skill.name.replace("-", "_")
    target.write_text(TEMPLATE.format(name=skill.name, path=target, slug=slug))
    target.chmod(0o755)
    print(f"wrote {target}")
    return 0


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skillcheck", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    test = sub.add_parser("test", help="run skill tests")
    test.add_argument("paths", nargs="*", help="test files or directories")
    test.add_argument("pytest_args", nargs=argparse.REMAINDER, help="arguments passed to pytest")
    test.set_defaults(func=cmd_test)

    status = sub.add_parser("status", help="show what was tested, and when")
    status.add_argument("paths", nargs="*", help="directories to search for results.json")
    status.set_defaults(func=cmd_status)

    report = sub.add_parser("report", help="pass rate, time, and cost per skill")
    report.add_argument("paths", nargs="*", help="directories to search for results.json")
    report.set_defaults(func=cmd_report)

    linter = sub.add_parser("lint", help="check skills without running a model")
    linter.add_argument("paths", nargs="*", help="skill directories, or a tree to search")
    linter.add_argument("--strict", action="store_true", help="treat warnings as errors")
    linter.add_argument("--ignore", action="append", help="a finding code to ignore, repeatable")
    linter.set_defaults(func=cmd_lint)

    tapes = sub.add_parser("cassettes", help="list or delete recorded runs")
    tapes.add_argument("--clean", action="store_true", help="delete every recorded run")
    tapes.set_defaults(func=cmd_cassettes)

    doctor = sub.add_parser("doctor", help="show which harnesses are installed")
    doctor.set_defaults(func=cmd_doctor)

    new = sub.add_parser("new-test", help="scaffold test.py inside a skill")
    new.add_argument("skill", help="path to the skill directory")
    new.add_argument("--force", action="store_true")
    new.set_defaults(func=cmd_new)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main_cli())
