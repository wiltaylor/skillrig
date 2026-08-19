"""Settings, resolved from the environment, pyproject.toml, then defaults.

Environment wins so a one-off run needs no file edit:

    SKILLRIG_HARNESS=all ./skills/git-graveyard/test.py
"""

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Tried in order; the first CLI actually installed is the default.
HARNESS_ORDER = ("claude", "codex", "opencode")

DEFAULT_TIMEOUT = 900
DEFAULT_JUDGE_MODEL = "sonnet"


def _pyproject(start: Path) -> dict:
    """`[tool.skillrig]` from the nearest pyproject.toml at or above `start`."""
    for directory in [start, *start.parents]:
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            try:
                data = tomllib.loads(candidate.read_text())
            except tomllib.TOMLDecodeError:
                return {}
            return data.get("tool", {}).get("skillrig", {})
    return {}


def installed(names=HARNESS_ORDER) -> list[str]:
    """Which harness CLIs are on PATH, in preference order."""
    from .harnesses import HARNESSES

    return [name for name in names if shutil.which(HARNESSES[name].binary)]


@dataclass
class Config:
    """Everything a run needs to know that is not the test itself."""

    harnesses: list[str] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=dict)
    judge: str = "claude"
    judge_model: str = DEFAULT_JUDGE_MODEL
    timeout: int = DEFAULT_TIMEOUT
    results: str | None = None
    record: bool = True

    def model_for(self, harness: str) -> str | None:
        return self.models.get(harness) or self.models.get("*")

    @classmethod
    def load(cls, root: Path | None = None) -> "Config":
        table = _pyproject(root or Path.cwd())
        environment = os.environ

        raw = environment.get("SKILLRIG_HARNESS") or table.get("harness")
        if raw in (None, "", "auto"):
            harnesses = installed()[:1] or list(HARNESS_ORDER[:1])
        elif raw == "all":
            harnesses = list(HARNESS_ORDER)
        elif isinstance(raw, list):
            harnesses = raw
        else:
            harnesses = [name.strip() for name in str(raw).split(",") if name.strip()]

        models = dict(table.get("models", {}))
        if shared := environment.get("SKILLRIG_MODEL"):
            models["*"] = shared
        for name in HARNESS_ORDER:
            if specific := environment.get(f"SKILLRIG_MODEL_{name.upper()}"):
                models[name] = specific

        return cls(
            harnesses=harnesses,
            models=models,
            judge=environment.get("SKILLRIG_JUDGE", table.get("judge", "claude")),
            judge_model=environment.get(
                "SKILLRIG_JUDGE_MODEL", table.get("judge_model", DEFAULT_JUDGE_MODEL)
            ),
            timeout=int(environment.get("SKILLRIG_TIMEOUT", table.get("timeout", DEFAULT_TIMEOUT))),
            results=environment.get("SKILLRIG_RESULTS", table.get("results")),
            record=environment.get("SKILLRIG_RECORD", "1") not in ("0", "false", "no"),
        )
