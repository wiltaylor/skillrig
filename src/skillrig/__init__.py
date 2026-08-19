"""Test agent skills against real coding agents.

    # skills/my-skill/test.py
    #!/usr/bin/env -S uv run --script
    # /// script
    # dependencies = ["skillrig"]
    # ///
    from skillrig import main

    def test_it_works(run_skill):
        result = run_skill("Do the thing")
        assert result.exists("output.txt")

    if __name__ == "__main__":
        raise SystemExit(main(__file__))
"""

from .cli import main
from .harnesses import HARNESSES, Harness, RunResult, ToolUse, get_harness
from .judge import Verdict, judge

__all__ = [
    "main",
    "judge",
    "Verdict",
    "Harness",
    "RunResult",
    "ToolUse",
    "HARNESSES",
    "get_harness",
]
