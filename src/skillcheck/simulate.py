"""A user the agent can talk to, played by a model.

`answers=["yes, go ahead"]` only works when you already know what the skill will
ask and in what order. A simulated user is given a brief -- who they are and what
they want -- and answers whatever the agent actually asks, so the test survives
the skill rewording its questions.
"""

from collections.abc import Callable

# Imported by name: the package exports a `judge` function, which shadows the
# module of the same name for anything that reaches for it through the package.
from .judge import ask

TEMPLATE = """You are playing a user talking to a coding agent. Stay in character
and reply exactly as that user would, in one or two sentences.

<who_you_are>
{brief}
</who_you_are>

<conversation_so_far>
{history}
</conversation_so_far>

The agent has just said:
<agent_message>
{message}
</agent_message>

If the agent is asking you something, answer it from the brief. If the brief does
not say, pick the most reasonable answer a user like that would give, and never
invent a requirement the brief does not support.

If the agent is not asking anything -- it has finished, or it is only reporting
what it did -- reply with exactly {sentinel} and nothing else.
"""

SENTINEL = "NOTHING_FURTHER"


class User:
    """A callable that answers an agent's hand-back, or ends the conversation."""

    def __init__(
        self,
        brief: str,
        backend: str | Callable[[str, str, int], str] = "claude",
        model: str = "sonnet",
        timeout: int = 300,
    ):
        self.brief = brief
        self.backend = backend
        self.model = model
        self.timeout = timeout
        # Every exchange, for a test that wants to assert on what the user said.
        self.said: list[str] = []
        self.heard: list[str] = []

    def history(self) -> str:
        lines = []
        for agent, user in zip(self.heard, self.said, strict=False):
            lines.append(f"agent: {agent.strip()[:1500]}\nyou: {user}")
        return "\n\n".join(lines) or "(nothing yet)"

    def __call__(self, message: str) -> str | None:
        prompt = TEMPLATE.format(
            brief=self.brief.strip(),
            history=self.history(),
            message=message.strip()[:4000],
            sentinel=SENTINEL,
        )
        reply = ask(prompt, backend=self.backend, model=self.model, timeout=self.timeout)
        self.heard.append(message)
        if SENTINEL in reply:
            return None
        self.said.append(reply)
        return reply
