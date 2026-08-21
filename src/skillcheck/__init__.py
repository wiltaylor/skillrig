"""Test agent skills against real coding agents.

# skills/my-skill/test.py
#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pytest-skillcheck"]
# ///
from skillcheck import main

def test_it_works(run_skill):
    result = run_skill("Do the thing")
    assert result.exists("output.txt")

if __name__ == "__main__":
    raise SystemExit(main(__file__))
"""

from .cli import main
from .harnesses import HARNESSES, Commit, Harness, RunResult, ToolUse, get_harness
from .judge import Comparison, Verdict, compare, judge
from .lint import Finding, lint_skill
from .runset import RunSet
from .simulate import User

__all__ = [
    "main",
    "judge",
    "compare",
    "Verdict",
    "Comparison",
    "Harness",
    "RunResult",
    "RunSet",
    "ToolUse",
    "Commit",
    "User",
    "Finding",
    "lint_skill",
    "HARNESSES",
    "get_harness",
]
