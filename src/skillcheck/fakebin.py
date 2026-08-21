"""Fake command-line binaries, for skills that call out to real services.

The agent runs its tools in its own shell, in its own process, so nothing that
patches Python's `subprocess` can intercept them. What does work is putting a
stub earlier on PATH than the real thing, which is what this does.

Every stub is strict: it answers what the test set up and refuses anything else
loudly, so a skill reaching for an unanticipated command fails the test instead
of doing something real. Every call is logged for the test to assert on.
"""

import json
import shutil
import stat
from pathlib import Path

from .harnesses import FAKE_BIN, FAKE_STATE, Harness

FAKES = Path(__file__).parent / "fakes"
MARKER = "skillcheck-fake"

# Nothing in the run should reach a real forge, so git URLs for the common hosts
# rewrite to a path that does not exist. This file also replaces the user's global
# git config for the run, so it carries an identity: without one, commits fail and
# the agent starts improvising with the real user's name and email.
GITCONFIG = """\
[user]
\tname = skillcheck
\temail = skillcheck@example.invalid
[init]
\tdefaultBranch = main
[safe]
\tdirectory = *
[url "/nonexistent/blocked-by-skillcheck/"]
\tinsteadOf = https://github.com/
[url "/nonexistent/blocked-by-skillcheck/"]
\tinsteadOf = git@github.com:
[url "/nonexistent/blocked-by-skillcheck/"]
\tinsteadOf = https://gitlab.com/
"""


def available() -> list[str]:
    """Which binaries skillcheck ships a fake for."""
    return sorted(path.stem for path in FAKES.glob("*.py") if path.stem != "__init__")


def install(workspace: Path, binary: str, fixture: dict, script: Path | None = None) -> None:
    """Put a fake `binary` first on PATH, backed by `fixture`.

    `script` overrides the shipped fake, for a binary skillcheck does not know.
    """
    source = script or FAKES / f"{binary}.py"
    if not Path(source).is_file():
        raise FileNotFoundError(
            f"no fake for {binary!r}; skillcheck ships {available()}. "
            "Pass script= with your own, or add one upstream."
        )

    bin_dir = workspace / FAKE_BIN
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / binary
    shutil.copy2(source, stub)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    state = workspace / FAKE_STATE / binary
    state.mkdir(parents=True, exist_ok=True)
    (state / "fixture.json").write_text(json.dumps(fixture, indent=2) + "\n")
    (state / "calls.jsonl").touch()

    (workspace / FAKE_STATE / "gitconfig").write_text(GITCONFIG)


def verify(harness: Harness, workspace: Path, binary: str) -> None:
    """Fail before a model runs if PATH does not resolve `binary` to the stub."""
    import os
    import subprocess

    proc = subprocess.run(
        [binary, "--skillcheck-fake"],
        cwd=workspace,
        capture_output=True,
        text=True,
        env={**os.environ, **harness.environment(workspace)},
    )
    if proc.returncode != 0 or MARKER not in proc.stdout:
        raise RuntimeError(
            f"refusing to run: `{binary}` on PATH is not the skillcheck fake "
            f"(exit {proc.returncode}, stdout {proc.stdout.strip()!r}). "
            "The real one would act on real infrastructure."
        )


def verify_through_agent(harness: Harness, workspace: Path, binary: str, timeout: int) -> None:
    """Have the agent itself run the marker check in a throwaway session.

    Checking skillcheck's own subprocess environment proves nothing about the shell
    the agent runs tools in, which can rebuild PATH from a profile. This asks the
    model to run the command and report back, in a session that does nothing else.
    """
    probe = harness.run(
        f"Run the command `{binary} --skillcheck-fake` and reply with its output, nothing else.",
        workspace,
        timeout,
    )
    if MARKER not in probe.output:
        raise RuntimeError(
            f"refusing to run: the agent's own shell did not resolve `{binary}` to the "
            f"skillcheck fake. It reported: {probe.output.strip()[:400]!r}"
        )
