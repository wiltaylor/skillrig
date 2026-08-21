"""Grade a run against a written rubric, using a model.

Two backends ship: the Claude Code CLI (default, no API key needed if you are
already signed in) and the Anthropic API. Supply your own with `judge_with`.
"""

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "score": {"type": "integer", "minimum": 1, "maximum": 5},
        "reasoning": {"type": "string"},
    },
    "required": ["passed", "score", "reasoning"],
}

TEMPLATE = """You are grading the output of a coding agent against a rubric.

<rubric>
{rubric}
</rubric>

<agent_run>
{context}
</agent_run>

Grade only against the rubric. Ignore style choices the rubric does not mention.
Score 1 (fails the rubric outright) to 5 (meets every point). `passed` is true
only when the run satisfies every requirement in the rubric.
Report the verdict with structured output.
"""


@dataclass
class Verdict:
    passed: bool
    score: int
    reasoning: str

    def __bool__(self) -> bool:
        return self.passed

    def __str__(self) -> str:
        return f"[{'pass' if self.passed else 'FAIL'} {self.score}/5] {self.reasoning}"


def _claude_cli(prompt: str, model: str, timeout: int) -> dict:
    proc = subprocess.run(
        [
            "claude",
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "json",
            "--allowed-tools",
            "StructuredOutput",
            "--no-session-persistence",
            "--json-schema",
            json.dumps(SCHEMA),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"judge failed ({proc.returncode}): {proc.stderr[-2000:]}")
    payload = json.loads(proc.stdout)
    result = payload[-1] if isinstance(payload, list) else payload
    verdict = result.get("structured_output")
    if not verdict:
        raise RuntimeError(f"judge returned no structured output: {result.get('result')!r}")
    return verdict


def _anthropic_api(prompt: str, model: str, timeout: int) -> dict:
    try:
        import anthropic
    except ImportError as error:  # pragma: no cover - depends on the user's environment
        raise RuntimeError(
            "the anthropic judge needs the anthropic package: pip install anthropic"
        ) from error

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), timeout=timeout)
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        tools=[
            {
                "name": "verdict",
                "description": "Report the grading verdict.",
                "input_schema": SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": "verdict"},
    )
    for block in message.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("judge returned no verdict")


BACKENDS: dict[str, Callable[[str, str, int], dict]] = {
    "claude": _claude_cli,
    "anthropic": _anthropic_api,
}


def judge(
    rubric: str,
    context: str,
    backend: str | Callable[[str, str, int], dict] = "claude",
    model: str = "sonnet",
    timeout: int = 300,
) -> Verdict:
    """Grade `context` against `rubric` and return the verdict."""
    call = BACKENDS[backend] if isinstance(backend, str) else backend
    if isinstance(backend, str) and backend not in BACKENDS:
        raise KeyError(f"unknown judge backend {backend!r}; pick from {sorted(BACKENDS)}")

    verdict = call(TEMPLATE.format(rubric=rubric.strip(), context=context.strip()), model, timeout)
    return Verdict(verdict["passed"], verdict["score"], verdict["reasoning"])
