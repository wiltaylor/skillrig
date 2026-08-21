#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Stand-in for curl, so a skill that fetches something can be tested offline.

The fixture maps a URL pattern to what the server would have said:

    fake={"curl": {
        "https://api.example.com/repos/.*": {"body": '{"name": "proj"}'},
        "https://example.com/missing": {"status": 404, "body": "not found"},
    }}

Patterns are regular expressions matched against the whole URL. `-o FILE` writes
the body to that file, `-w '%{http_code}'` appends the status, and `-f` exits 22
on a 4xx or 5xx the way real curl does. Every call is logged for the test.

Anything the fixture does not describe is refused rather than fetched.
"""

import json
import os
import re
import sys
from pathlib import Path

MARKER = "skillcheck-fake"

STATE = Path(os.environ["SKILLCHECK_FAKE_STATE"]) / "curl"
FIXTURE = STATE / "fixture.json"
CALLS = STATE / "calls.jsonl"

# Flags that swallow the argument after them, so it is never mistaken for the URL.
TAKES_VALUE = {
    "-o", "--output", "-X", "--request", "-d", "--data", "--data-raw", "-H", "--header",
    "-w", "--write-out", "-u", "--user", "-A", "--user-agent", "-b", "--cookie",
    "-e", "--referer", "-T", "--upload-file", "--max-time", "-m", "--connect-timeout",
    "--retry", "--url",
}  # fmt: skip


def log(status: str) -> None:
    with CALLS.open("a") as handle:
        handle.write(json.dumps({"argv": sys.argv[1:], "status": status}) + "\n")


def refuse(message: str) -> None:
    log("refused")
    print(f"fake curl: {message}", file=sys.stderr)
    print(f"fake curl: this stub only answers what the test set up ({MARKER})", file=sys.stderr)
    sys.exit(64)


def routes() -> dict:
    fixture = json.loads(FIXTURE.read_text())
    return fixture.get("urls", fixture) if isinstance(fixture, dict) else {}


def parse(arguments: list[str]) -> tuple[str | None, dict]:
    """The URL, and the flags this fake acts on."""
    options: dict = {"output": None, "write_out": None, "fail": False}
    url = None
    skip = False
    for index, argument in enumerate(arguments):
        if skip:
            skip = False
            continue
        if argument in ("-o", "--output"):
            options["output"] = arguments[index + 1] if index + 1 < len(arguments) else None
            skip = True
        elif argument in ("-w", "--write-out"):
            options["write_out"] = arguments[index + 1] if index + 1 < len(arguments) else None
            skip = True
        elif argument in ("--url",):
            url = arguments[index + 1] if index + 1 < len(arguments) else None
            skip = True
        elif argument in TAKES_VALUE:
            skip = True
        elif argument in ("-f", "--fail"):
            options["fail"] = True
        elif argument.startswith("-"):
            continue
        elif url is None:
            url = argument
    return url, options


def main() -> None:
    arguments = sys.argv[1:]
    if arguments[:1] == ["--skillcheck-fake"]:
        log("ok")
        print(MARKER)
        return

    url, options = parse(arguments)
    if not url:
        refuse("called with no URL")

    for pattern, response in routes().items():
        if not re.match(pattern, url):
            continue
        if isinstance(response, str):
            response = {"body": response}
        status = int(response.get("status", 200))
        body = response.get("body", "")

        log("ok" if status < 400 else "error")
        if options["output"]:
            target = Path(options["output"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        else:
            sys.stdout.write(body)
        if options["write_out"]:
            sys.stdout.write(options["write_out"].replace("%{http_code}", str(status)))
        sys.exit(22 if options["fail"] and status >= 400 else 0)

    refuse(f"no fixture matches the URL: {url}")


if __name__ == "__main__":
    main()
