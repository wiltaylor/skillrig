#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Stand-in for the GitHub CLI, so a skill that talks to GitHub can be tested.

Strict on purpose: it answers the commands a test set up and refuses everything
else loudly, so a skill reaching for an unexpected GitHub call fails the test
instead of quietly doing something real. Every invocation is appended to
calls.jsonl for the test to assert on.

`gh --skillrig-fake` prints a marker. skillrig checks for it before letting a
model run, so a broken PATH is caught while it is still harmless.

The fixture maps "owner/name" to whatever fields the test wants answered:

    {"wiltaylor/deadproj": {"visibility": "PUBLIC", "contents": ["old-thing"]}}
"""

import json
import os
import sys
from pathlib import Path

MARKER = "skillrig-fake"

STATE = Path(os.environ["SKILLRIG_FAKE_STATE"]) / "gh"
FIXTURE = STATE / "fixture.json"
CALLS = STATE / "calls.jsonl"


def log(status: str) -> None:
    with CALLS.open("a") as handle:
        handle.write(json.dumps({"argv": sys.argv[1:], "status": status}) + "\n")


def refuse(message: str) -> None:
    log("refused")
    print(f"fake gh: {message}", file=sys.stderr)
    print(f"fake gh: this stub only answers what the test expects ({MARKER})", file=sys.stderr)
    sys.exit(64)


def repos() -> dict:
    return json.loads(FIXTURE.read_text())


def save(data: dict) -> None:
    FIXTURE.write_text(json.dumps(data, indent=2) + "\n")


def query(args: list[str]) -> str | None:
    for flag in ("-q", "--jq"):
        if flag in args:
            return args[args.index(flag) + 1]
    return None


def missing(name: str) -> None:
    log("not-found")
    print(f"fake gh: no such repo in the test fixture: {name}", file=sys.stderr)
    sys.exit(1)


def repo_view(args: list[str]) -> None:
    name = args[0] if args else ""
    entry = repos().get(name)
    if entry is None or entry.get("deleted"):
        missing(name)

    fields = args[args.index("--json") + 1].split(",") if "--json" in args else []
    payload = {field: entry.get(field) for field in fields} if fields else entry

    log("ok")
    expression = query(args)
    if expression and expression.startswith("."):
        print(payload.get(expression.lstrip("."), ""))
    else:
        print(json.dumps(payload))


def repo_delete(args: list[str]) -> None:
    name = args[0] if args else ""
    if "--yes" not in args:
        refuse("repo delete without --yes")
    data = repos()
    if name not in data:
        missing(name)
    data[name]["deleted"] = True
    save(data)
    log("deleted")
    print(f"✓ Deleted repository {name}")


def api(args: list[str]) -> None:
    path = args[0] if args else ""
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "repos" or parts[3] != "contents":
        refuse(f"unsupported api path: {path}")

    name = f"{parts[1]}/{parts[2]}"
    entry = repos().get(name)
    if entry is None or entry.get("deleted"):
        missing(name)

    contents = [{"name": item, "type": "dir"} for item in entry.get("contents", [])]
    log("ok")
    if query(args) == ".[].name":
        print("\n".join(item["name"] for item in contents))
    else:
        print(json.dumps(contents))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        refuse("called with no arguments")

    if args[0] == "--skillrig-fake":
        log("ok")
        print(MARKER)
        return
    if args[:2] == ["repo", "view"]:
        return repo_view(args[2:])
    if args[:2] == ["repo", "delete"]:
        return repo_delete(args[2:])
    if args[0] == "api":
        return api(args[1:])

    refuse(f"unsupported command: {' '.join(args)}")


if __name__ == "__main__":
    main()
