#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""A fake binary described by the test rather than written in Python.

Used for any command skillcheck does not ship a purpose-built fake for. The
fixture maps a pattern, matched against the arguments as one string, to what the
command should print:

    fake={"kubectl": {
        "get pods": "NAME   READY\\nweb-0  1/1",
        "apply -f .*": {"stdout": "deployment created", "exit": 0},
        "delete .*": {"stderr": "forbidden", "exit": 1},
    }}

Patterns are regular expressions anchored at the start, tried in the order
written. A value may also be a list, and then each call takes the next entry --
for a command that reports something different once the work has finished.

Anything the fixture does not describe is refused, so a skill reaching for a
command the test never anticipated fails loudly instead of doing something real.
"""

import json
import os
import re
import sys
from pathlib import Path

MARKER = "skillcheck-fake"

NAME = Path(sys.argv[0]).name
STATE = Path(os.environ["SKILLCHECK_FAKE_STATE"]) / NAME
FIXTURE = STATE / "fixture.json"
CALLS = STATE / "calls.jsonl"
COUNTERS = STATE / "counters.json"


def log(status: str) -> None:
    with CALLS.open("a") as handle:
        handle.write(json.dumps({"argv": sys.argv[1:], "status": status}) + "\n")


def refuse(message: str) -> None:
    log("refused")
    print(f"fake {NAME}: {message}", file=sys.stderr)
    print(f"fake {NAME}: this stub only answers what the test set up ({MARKER})", file=sys.stderr)
    sys.exit(64)


def commands() -> dict:
    fixture = json.loads(FIXTURE.read_text())
    # `commands` lets a fixture carry other settings beside the command map.
    return fixture.get("commands", fixture) if isinstance(fixture, dict) else {}


def bump(pattern: str) -> int:
    counts = json.loads(COUNTERS.read_text()) if COUNTERS.is_file() else {}
    index = counts.get(pattern, 0)
    counts[pattern] = index + 1
    COUNTERS.write_text(json.dumps(counts))
    return index


def respond(pattern: str, response) -> None:
    if isinstance(response, list):
        if not response:
            refuse(f"the fixture for {pattern!r} is empty")
        response = response[min(bump(pattern), len(response) - 1)]
    if isinstance(response, str):
        response = {"stdout": response}
    if not isinstance(response, dict):
        refuse(f"the fixture for {pattern!r} is not a string, list, or object")

    for relative, content in (response.get("writes") or {}).items():
        target = Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    code = int(response.get("exit", 0))
    log("ok" if code == 0 else "error")
    if response.get("stdout"):
        print(response["stdout"])
    if response.get("stderr"):
        print(response["stderr"], file=sys.stderr)
    sys.exit(code)


def main() -> None:
    arguments = sys.argv[1:]
    if arguments[:1] == ["--skillcheck-fake"]:
        log("ok")
        print(MARKER)
        return
    if not arguments:
        refuse("called with no arguments")

    line = " ".join(arguments)
    for pattern, response in commands().items():
        if re.match(pattern, line):
            respond(pattern, response)
    refuse(f"no fixture matches: {line}")


if __name__ == "__main__":
    main()
