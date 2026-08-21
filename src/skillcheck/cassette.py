"""Recorded runs, so the assertions can be re-run without paying for a model.

A cassette holds what the CLI printed and what the workspace looked like
afterwards. Replaying rebuilds the `RunResult` from that, which makes every
assertion in the suite runnable in CI for free -- the model call is the only part
that cost anything.

A cassette is keyed by everything that could change the answer: the harness, the
model, the prompt, the skill's own files, and whatever the test put in the
workspace. Edit the skill and the key changes, so a stale recording is never
replayed against new wording.
"""

import base64
import hashlib
import json
from pathlib import Path

from .harnesses import FAKE_STATE, Harness, RunResult, merge_turns, snapshot

MODES = ("off", "auto", "record", "replay")
DIRNAME = ".skillcheck/cassettes"


# Written by skillcheck itself, and so no part of what the skill says to do. A
# recording of a run would otherwise be invalidated by the act of recording it.
NOT_THE_SKILL = {"results.json", "__pycache__"}


def tree_digest(path: Path) -> str:
    """A hash over every file in a directory, contents and names alike."""
    hasher = hashlib.sha256()
    for file in sorted(Path(path).rglob("*")):
        relative = file.relative_to(path)
        if not file.is_file() or NOT_THE_SKILL & set(relative.parts):
            continue
        hasher.update(str(relative).encode())
        hasher.update(file.read_bytes())
    return hasher.hexdigest()


def key(
    harness: Harness,
    prompt: str,
    skills: list[Path],
    files: dict[str, str] | None,
    answers,
    fake: dict | None,
) -> str | None:
    """The identity of a run, or None when it cannot be recorded.

    A run whose answers come from a callable -- a simulated user -- has no fixed
    identity, so it is never cached.
    """
    if callable(answers):
        return None
    material = json.dumps(
        {
            "harness": harness.name,
            "model": harness.model,
            "prompt": prompt,
            "skills": sorted(tree_digest(path) for path in skills),
            "files": files or {},
            "answers": answers if not isinstance(answers, dict) else sorted(answers.items()),
            "fake": fake or {},
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def path_for(root: Path, harness: str, identity: str, override: str | None = None) -> Path:
    directory = Path(override) if override else Path(root) / DIRNAME
    return directory / harness / f"{identity}.json"


def _files(workspace: Path) -> dict[str, dict]:
    stored = {}
    for relative in snapshot(workspace):
        raw = (workspace / relative).read_bytes()
        try:
            stored[relative] = {"text": raw.decode()}
        except UnicodeDecodeError:
            stored[relative] = {"b64": base64.b64encode(raw).decode()}
    return stored


def _fake_calls(workspace: Path) -> dict[str, str]:
    state = workspace / FAKE_STATE
    if not state.is_dir():
        return {}
    return {
        log.parent.name: log.read_text() for log in state.glob("*/calls.jsonl") if log.is_file()
    }


def save(path: Path, result: RunResult) -> None:
    """Write a run to disk, contents of the workspace included."""
    turns = result.turn_results or [result]
    payload = {
        "harness": result.harness,
        "model": result.model,
        "prompt": result.prompt,
        "turns": [
            {
                "prompt": turn.prompt,
                "stdout": turn.stdout,
                "stderr": turn.stderr,
                "exit_code": turn.exit_code,
                "duration_s": round(turn.duration_s, 3),
                "before": turn.before,
                "after": turn.after,
            }
            for turn in turns
        ],
        "before": result.before,
        "files": _files(result.workspace),
        "fake_calls": _fake_calls(result.workspace),
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2) + "\n")


def restore_workspace(workspace: Path, payload: dict) -> None:
    """Make the workspace match the recording, additions and deletions alike."""
    wanted = payload.get("files", {})
    for relative in snapshot(workspace):
        if relative not in wanted:
            (workspace / relative).unlink()

    for relative, stored in wanted.items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if "text" in stored:
            target.write_text(stored["text"])
        else:
            target.write_bytes(base64.b64decode(stored["b64"]))

    for binary, log in payload.get("fake_calls", {}).items():
        directory = workspace / FAKE_STATE / binary
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "calls.jsonl").write_text(log)


def load(path: Path, harness: Harness, workspace: Path, skill: str = "") -> RunResult | None:
    """Rebuild a recorded run, and put the workspace back the way it ended."""
    if not Path(path).is_file():
        return None
    payload = json.loads(Path(path).read_text())
    restore_workspace(workspace, payload)

    turns = []
    for turn in payload["turns"]:
        output, tool_uses, events = harness.parse(turn["stdout"])
        cost, read, written = harness.usage(events)
        turns.append(
            RunResult(
                harness=payload["harness"],
                prompt=turn["prompt"],
                workspace=workspace,
                exit_code=turn["exit_code"],
                duration_s=turn["duration_s"],
                output=output,
                tool_uses=tool_uses,
                stdout=turn["stdout"],
                stderr=turn["stderr"],
                events=events,
                model=payload.get("model", "default"),
                before=turn.get("before") or payload.get("before", {}),
                after=turn.get("after"),
                skill=skill,
                cost_usd=cost,
                input_tokens=read,
                output_tokens=written,
            )
        )
    return merge_turns(turns)


def clean(root: Path, override: str | None = None) -> int:
    """Delete every recorded run. Returns how many were removed."""
    directory = Path(override) if override else Path(root) / DIRNAME
    files = list(directory.rglob("*.json")) if directory.is_dir() else []
    for file in files:
        file.unlink()
    return len(files)
