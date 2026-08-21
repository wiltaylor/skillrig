"""Adapters that run one prompt against one coding agent CLI.

Each adapter turns a prompt into a subprocess invocation and normalises whatever
that CLI streams back into a `RunResult`, so a test asserts against the same
shape no matter which agent produced it.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

# Workspace-relative directories and files that belong to a harness, not a test.
HARNESS_DIRS = {".claude", ".agents", ".codex", ".opencode", ".factory", ".git", ".skillcheck"}
HARNESS_FILES = {"opencode.json"}

# Where fake binaries and their state live inside the workspace.
FAKE_BIN = ".skillcheck/bin"
FAKE_STATE = ".skillcheck/state"


# How many turns a conversation may take before skillcheck stops answering. Only
# reached by matched or simulated answers: a plain list of answers is finite.
DEFAULT_MAX_TURNS = 8

# The parts of a run a judge can be shown.
SECTIONS = ("prompt", "questions", "tools", "files", "answer")
DEFAULT_SECTIONS = ("prompt", "questions", "tools", "answer")


@dataclass
class ToolUse:
    name: str
    input: dict

    def text(self) -> str:
        """The call as one searchable string: tool name and its arguments."""
        return f"{self.name} {json.dumps(self.input, default=str)}"


@dataclass
class Commit:
    sha: str
    subject: str
    body: str = ""

    def text(self) -> str:
        return f"{self.subject}\n{self.body}".strip()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(workspace: Path) -> dict[str, str]:
    """Hash every file in the workspace, ignoring harness bookkeeping.

    Taken before the agent runs, so afterwards a test can say what it created,
    changed, or left alone -- which "the file exists" cannot.
    """
    state = {}
    for path in sorted(Path(workspace).rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(workspace)
        if HARNESS_DIRS & set(relative.parts) or str(relative) in HARNESS_FILES:
            continue
        state[str(relative)] = digest(path)
    return state


@dataclass
class RunResult:
    """One conversation with an agent, and what it did to the workspace."""

    harness: str
    prompt: str
    workspace: Path
    exit_code: int
    duration_s: float
    output: str
    tool_uses: list[ToolUse]
    stdout: str
    stderr: str
    events: list[dict] = field(default_factory=list)
    model: str = "default"
    # What the agent said each time it handed control back and got an answer.
    handbacks: list[str] = field(default_factory=list)
    # Tool calls from the first turn: everything done before the user replied.
    opening_tool_uses: list[ToolUse] = field(default_factory=list)
    turns: int = 1
    # Each turn on its own, oldest first, for tests that assert on one of them.
    turn_results: list["RunResult"] = field(default_factory=list)
    # The workspace as it was before the agent ran: path -> content hash.
    before: dict[str, str] = field(default_factory=dict)
    # The workspace as it was when this turn ended. Unset on a whole run, which
    # reads the workspace as it is now; set on each turn, so what a later turn
    # wrote does not appear in an earlier turn's diff.
    after: dict[str, str] | None = None
    # Binaries faked for this run, so a failure can report what they refused.
    fakes: list[str] = field(default_factory=list)
    # The skill under test, by directory name.
    skill: str = ""
    cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def turn(self, index: int) -> "RunResult":
        """One turn of the conversation, in isolation.

        Turn 0 is everything the agent did before the user said anything.
        """
        if not self.turn_results:
            if index in (0, -1):
                return self
            raise IndexError(f"this run has one turn, so turn {index} does not exist")
        return self.turn_results[index]

    @property
    def questions(self) -> list[str]:
        """The hand-backs that put something to the user.

        Not every hand-back is a question: an agent that finishes the work and
        reports back also hands over. Judging those as questions reads a "done"
        report as the skill acting before it asked.
        """
        return [message for message in self.handbacks if asks_question(message)]

    def read(self, relative: str) -> str:
        return (self.workspace / relative).read_text()

    def exists(self, relative: str) -> bool:
        return (self.workspace / relative).exists()

    def files(self) -> list[str]:
        """Every file in the workspace, excluding harness bookkeeping."""
        found = []
        for path in sorted(self.workspace.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.workspace)
            if HARNESS_DIRS & set(rel.parts) or str(rel) in HARNESS_FILES:
                continue
            found.append(str(rel))
        return found

    def ran(self, pattern: str) -> bool:
        """True when any shell command the agent ran matches `pattern`."""
        return any(
            re.search(pattern, str(use.input.get("command", "")))
            for use in self.tool_uses
            if use.name.lower() in ("bash", "shell")
        )

    def used_skill(self, name: str) -> bool:
        return any(
            use.name.lower() == "skill" and name in json.dumps(use.input) for use in self.tool_uses
        )

    def used_agent(self, name: str) -> bool:
        """True when the run delegated to the named subagent.

        Only meaningful on a harness that names its subagents; use `delegated()`
        where it does not.
        """
        return any(
            use.name.lower() in ("task", "agent") and use.input.get("subagent_type") == name
            for use in self.tool_uses
        )

    def delegated(self) -> bool:
        """True when the run handed work to any subagent."""
        return any(use.name.lower() in ("task", "agent", "collab") for use in self.tool_uses)

    def asked_question(self) -> bool:
        return bool(self.questions) or asks_question(self.output)

    def asked(self, pattern: str) -> bool:
        """True when the agent put something matching `pattern` to the user.

        Narrower than `asked_question`, which only says that some question was
        asked: this says the agent asked about the right thing.
        """
        return any(re.search(pattern, text, re.IGNORECASE) for text in self.all_questions)

    @property
    def all_questions(self) -> list[str]:
        """Every message that put something to the user, including the last one."""
        found = list(self.questions)
        if asks_question(self.output):
            found.append(self.output)
        return found

    def said(self, pattern: str) -> bool:
        """True when anything the agent said back matches `pattern`."""
        return any(
            re.search(pattern, text, re.IGNORECASE) for text in [*self.handbacks, self.output]
        )

    def ordered(self, *patterns: str) -> bool:
        """True when tool calls matching these patterns happened in this order.

        Each pattern is matched against `"<tool name> <arguments as json>"`, and
        matches may have other calls between them -- only the order is asserted.
        """
        remaining = list(patterns)
        for use in self.tool_uses:
            if remaining and re.search(remaining[0], use.text(), re.IGNORECASE):
                remaining.pop(0)
        return not remaining

    def tool_count(self, name: str) -> int:
        """How many times a tool was called, by name."""
        return sum(1 for use in self.tool_uses if use.name.lower() == name.lower())

    def read_files(self) -> list[str]:
        """Workspace-relative paths the agent read, in order, without duplicates."""
        found = []
        for use in self.tool_uses:
            if use.name.lower() not in ("read", "view", "cat"):
                continue
            path = use.input.get("file_path") or use.input.get("path") or use.input.get("filePath")
            if not path:
                continue
            text = str(path)
            with_prefix = str(self.workspace) + "/"
            relative = text[len(with_prefix) :] if text.startswith(with_prefix) else text
            if relative not in found:
                found.append(relative)
        return found

    def loaded_skill(self, name: str | None = None) -> bool:
        """True when the run actually picked the skill up.

        A skill is loaded through a tool on some CLIs and by reading its SKILL.md
        on others, so both count. With no name, the skill under test.
        """
        wanted = name or self.skill
        if not wanted:
            raise ValueError("no skill name to look for, and this run has no skill under test")
        if self.used_skill(wanted):
            return True
        marker = f"{wanted}/SKILL.md"
        return any(marker in use.text() for use in self.tool_uses)

    # -- what the run did to the workspace ----------------------------------

    def _after(self) -> dict[str, str]:
        return snapshot(self.workspace) if self.after is None else self.after

    def created(self) -> list[str]:
        after = self._after()
        return sorted(path for path in after if path not in self.before)

    def modified(self) -> list[str]:
        after = self._after()
        return sorted(
            path
            for path, hashed in after.items()
            if path in self.before and self.before[path] != hashed
        )

    def deleted(self) -> list[str]:
        after = self._after()
        return sorted(path for path in self.before if path not in after)

    def touched(self) -> list[str]:
        """Every path the run created, changed, or removed."""
        return sorted({*self.created(), *self.modified(), *self.deleted()})

    def untouched(self, *prefixes: str) -> bool:
        """True when nothing under any of these paths changed.

        With no arguments, true when the run changed nothing at all.
        """
        changed = self.touched()
        if not prefixes:
            return not changed
        return not any(
            path == prefix or path.startswith(prefix.rstrip("/") + "/")
            for path in changed
            for prefix in prefixes
        )

    # -- git ----------------------------------------------------------------

    def _git(self, *arguments: str) -> str:
        proc = subprocess.run(
            ["git", *arguments],
            cwd=self.workspace,
            capture_output=True,
            text=True,
        )
        return proc.stdout if proc.returncode == 0 else ""

    def commits(self, path: str = ".") -> list[Commit]:
        """Commits in the workspace repo, newest first."""
        raw = self._git("-C", path, "log", "--format=%H%x00%s%x00%b%x1e")
        found = []
        for entry in raw.split("\x1e"):
            entry = entry.strip("\n")
            if not entry:
                continue
            sha, subject, body = (entry.split("\x00") + ["", ""])[:3]
            found.append(Commit(sha, subject, body.strip()))
        return found

    def committed(self, pattern: str, path: str = ".") -> bool:
        """True when any commit message matches `pattern`."""
        return any(
            re.search(pattern, commit.text(), re.IGNORECASE) for commit in self.commits(path)
        )

    def branch(self, path: str = ".") -> str:
        """The branch checked out in the workspace repo."""
        return self._git("-C", path, "rev-parse", "--abbrev-ref", "HEAD").strip()

    def acted_before_asking(self, pattern: str) -> bool:
        """True when a tool call matching `pattern` ran before the first answer.

        Exact where a judge is not: the first turn is everything the agent did
        before the user said anything, so a match there is work done unasked.
        """
        return any(re.search(pattern, json.dumps(use.input)) for use in self.opening_tool_uses)

    def reached_home(self) -> bool:
        """True when a tool call touched the real home directory.

        On an isolated harness that means a globally installed copy of the skill
        shadowed the one under test, so the run graded the wrong files.
        """
        home = str(Path.home())
        return any(home in json.dumps(use.input) for use in self.tool_uses)

    def _fake_log(self, binary: str) -> list[dict]:
        log = self.workspace / FAKE_STATE / binary / "calls.jsonl"
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

    def calls(self, binary: str) -> list[list[str]]:
        """Every argument list a faked binary was invoked with, in order."""
        return [call["argv"] for call in self._fake_log(binary)]

    def called(self, binary: str, *arguments: str) -> bool:
        """True when a faked binary saw a call starting with these arguments."""
        return any(call[: len(arguments)] == list(arguments) for call in self.calls(binary))

    def refusals(self, binary: str) -> list[list[str]]:
        """Calls a faked binary refused: commands the test did not anticipate."""
        return [call["argv"] for call in self._fake_log(binary) if call["status"] == "refused"]

    def transcript(self, include: Sequence[str] = DEFAULT_SECTIONS) -> str:
        """The run as text for a judge, in whichever sections are asked for.

        Narrowing it is what keeps a rubric about the questions from being graded
        against a wall of tool calls.
        """
        unknown = [name for name in include if name not in SECTIONS]
        if unknown:
            raise ValueError(f"unknown transcript section {unknown}; pick from {list(SECTIONS)}")

        lines = []
        if "prompt" in include:
            lines.append(f"# Prompt\n{self.prompt}\n")
        if "questions" in include:
            for index, question in enumerate(self.all_questions, start=1):
                lines.append(f"# Question {index}\n{question}\n")
        if "tools" in include:
            lines.append("# Tool calls")
            for use in self.tool_uses:
                lines.append(f"- {use.name}: {json.dumps(use.input, default=str)[:600]}")
            lines.append("")
        if "files" in include:
            lines.append("# Files written")
            for relative in [*self.created(), *self.modified()]:
                lines.append(f"\n## {relative}\n{self._preview(relative)}")
            lines.append("")
        if "answer" in include:
            lines.append(f"# Final answer\n{self.output}")
        return "\n".join(lines).strip()

    def _preview(self, relative: str, limit: int = 4000) -> str:
        try:
            text = self.read(relative)
        except (UnicodeDecodeError, OSError):
            return "<not text>"
        return text[:limit] + ("\n...truncated" if len(text) > limit else "")


# Agents ask for input without a question mark as often as with one: "Confirm
# that I should proceed", "let me know which you prefer".
REQUESTS_INPUT = re.compile(
    r"\?|confirm |please confirm|let me know|shall i|should i|would you like|"
    r"tell me (which|what|where)|waiting for (your|you)|say the word",
    re.IGNORECASE,
)


def asks_question(text: str) -> bool:
    """Rough check that a message puts something to the user.

    Agents get no ask-the-user tool in print mode, so requests arrive as prose in
    whatever shape the model chose and no pattern catches every shape. Cheap and
    approximate on purpose: judge `result.handbacks` when a test's verdict turns
    on whether the agent really did stop and ask.
    """
    tail = "\n".join(text.strip().splitlines()[-15:])
    return bool(REQUESTS_INPUT.search(tail))


def merge_turns(turns: list[RunResult]) -> RunResult:
    """Fold a multi-turn conversation into the single result a test asserts on."""
    last = turns[-1]
    costs = [turn.cost_usd for turn in turns if turn.cost_usd is not None]
    return replace(
        last,
        prompt=turns[0].prompt,
        exit_code=next((turn.exit_code for turn in turns if turn.exit_code != 0), 0),
        duration_s=sum(turn.duration_s for turn in turns),
        tool_uses=[use for turn in turns for use in turn.tool_uses],
        stdout="\n".join(turn.stdout for turn in turns),
        stderr="\n".join(turn.stderr for turn in turns),
        events=[event for turn in turns for event in turn.events],
        handbacks=[turn.output for turn in turns[:-1]],
        opening_tool_uses=turns[0].tool_uses,
        turns=len(turns),
        turn_results=list(turns),
        before=turns[0].before,
        # A whole run reads the workspace as it is now, so a test can act on it
        # and ask again.
        after=None,
        cost_usd=sum(costs) if costs else None,
        input_tokens=sum(turn.input_tokens for turn in turns),
        output_tokens=sum(turn.output_tokens for turn in turns),
    )


class Replies:
    """Where each answer to the agent comes from, whatever form the test gave.

    Three forms, all ending up here: a list answers each hand-back in order, a
    mapping answers the hand-back whose text matches one of its patterns, and a
    callable is asked what to say -- which is how a simulated user plugs in.
    """

    def __init__(self, answers=None):
        self.answers = answers
        self.queue: list[str] = []
        self.pending: list[tuple[str, str]] = []
        if isinstance(answers, Mapping):
            self.pending = list(answers.items())
        elif isinstance(answers, (list, tuple)):
            if all(isinstance(item, str) for item in answers):
                self.queue = list(answers)
            else:
                self.pending = [tuple(item) for item in answers]
        elif answers is not None and not callable(answers):
            raise TypeError(f"answers must be a list, mapping, or callable, not {type(answers)}")
        # A mapping's patterns are consumed as they match, so a skill that keeps
        # asking the same thing ends the conversation rather than looping on it.
        self.matched: set[int] = set()

    def limit(self, max_turns: int) -> int:
        """A list of answers is finite, so it is never cut short by the cap."""
        return max(max_turns, len(self.queue) + 1)

    def next(self, question: str) -> str | None:
        """What to say back, or None to end the conversation."""
        if callable(self.answers):
            reply = self.answers(question)
            return reply or None
        if self.queue:
            return self.queue.pop(0)
        if not self.pending or not asks_question(question):
            return None
        for index, (pattern, reply) in enumerate(self.pending):
            if index in self.matched:
                continue
            if re.search(pattern, question, re.IGNORECASE):
                self.matched.add(index)
                return reply
        return None


def json_lines(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def find_value(events: list[dict], keys: tuple[str, ...]) -> str | None:
    """First non-empty string stored under any of `keys`, at any depth."""
    stack: list = list(events)
    while stack:
        node = stack.pop(0)
        if isinstance(node, dict):
            for key, value in node.items():
                if key in keys and isinstance(value, str) and value:
                    return value
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return None


def config_dir(root: Path, name: str, credentials: list[str]) -> Path:
    """A throwaway config directory with the real credential files linked in.

    Credentials are symlinked, never copied, so no secret is duplicated on disk.
    """
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    for relative in credentials:
        source = Path(relative).expanduser()
        link = directory / source.name
        if source.exists() and not link.exists():
            link.symlink_to(source)
    return directory


class Harness:
    """Base adapter. Subclasses supply the CLI invocation and output parsing."""

    name: str = ""
    binary: str = ""
    # Workspace-relative directories a skill is installed into.
    skill_dirs: tuple[str, ...] = ()
    # Workspace-relative directories a subagent definition is installed into.
    agent_dirs: tuple[str, ...] = ()
    # Whether the CLI has named subagents a test can assert on by name.
    names_agents: bool = True
    # Whether `isolate` hides globally installed skills and agents from the run.
    isolated: bool = False
    default_model: str | None = None

    def __init__(self, model: str | None = None, container: str | None = None):
        self.model = model or self.default_model
        # An image name runs the CLI inside a container instead of on this machine.
        self.container = container

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    # -- installing what the run is testing ---------------------------------

    def install_skill(self, workspace: Path, source: Path) -> None:
        source = Path(source)
        if not (source / "SKILL.md").is_file():
            raise FileNotFoundError(f"no SKILL.md in {source}")
        for directory in self.skill_dirs:
            target = workspace / directory / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, dirs_exist_ok=True)

    def install_agent(self, workspace: Path, source: Path) -> None:
        source = Path(source)
        if not self.agent_dirs:
            raise NotImplementedError(f"the {self.name} harness has no subagent directory")
        if not source.is_file():
            raise FileNotFoundError(f"no such agent definition: {source}")
        for directory in self.agent_dirs:
            target = workspace / directory / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self.render_agent(source.read_text()))

    def render_agent(self, definition: str) -> str:
        """The definition in this CLI's own format. Claude's is the source form."""
        return definition

    def agent_reference(self, agent: str) -> str:
        """How a prompt refers to an installed subagent on this CLI."""
        return f"the {agent} subagent"

    # -- environment --------------------------------------------------------

    def isolate(self, root: Path) -> dict[str, str]:
        """Point the CLI at a throwaway global config.

        Without it the agent reads globally installed skills from the home
        directory, and those shadow the copy under test — so a test silently
        grades whatever was last deployed rather than the working tree.
        """
        return {}

    def environment(self, workspace: Path) -> dict[str, str]:
        env = dict(self.isolate(workspace.parent))
        if (workspace / FAKE_BIN).is_dir():
            env["PATH"] = f"{workspace / FAKE_BIN}:{os.environ.get('PATH', '')}"
            env["SKILLCHECK_FAKE_STATE"] = str(workspace / FAKE_STATE)
            env["GIT_CONFIG_GLOBAL"] = str(workspace / FAKE_STATE / "gitconfig")
        return env

    def containerize(self, command: list[str], workspace: Path) -> list[str]:
        """The same command, run inside a container.

        `reached_home` tells you afterwards that a run touched the real machine.
        This stops it: the container sees the workspace, its throwaway config
        directory, and a read-only home for credentials, and nothing else.
        """
        root = workspace.parent
        home = Path.home()
        argv = [
            "docker",
            "run",
            "--rm",
            "-i",
            "-v",
            f"{root}:{root}",
            "-v",
            f"{home}:{home}:ro",
            "-w",
            str(workspace),
            "-e",
            f"HOME={home}",
        ]
        for key, value in self.environment(workspace).items():
            argv += ["-e", f"{key}={value}"]
        return [*argv, "-e", "CI=1", self.container, *command]

    def prepare(self, workspace: Path) -> None:
        """Anything the CLI needs in the workspace before the first turn."""

    # -- running ------------------------------------------------------------

    def command(self, prompt: str, workspace: Path) -> list[str]:
        raise NotImplementedError

    def resume_command(self, prompt: str, workspace: Path, session: str) -> list[str]:
        raise NotImplementedError(f"the {self.name} harness cannot resume a session")

    def session_id(self, events: list[dict]) -> str | None:
        return None

    def parse(self, stdout: str) -> tuple[str, list[ToolUse], list[dict]]:
        raise NotImplementedError

    def detect_model(self, events: list[dict]) -> str:
        return self.model or "default"

    def usage(self, events: list[dict]) -> tuple[float | None, int, int]:
        """What the turn cost: dollars where the CLI reports them, and tokens.

        A skill that doubles in cost after an edit is a regression the assertions
        would otherwise miss, so every harness reports what it can.
        """
        return None, 0, 0

    def _turn(self, command: list[str], prompt: str, workspace: Path, timeout: int) -> RunResult:
        started = time.monotonic()
        if self.container:
            command = self.containerize(command, workspace)
        proc = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "CI": "1", **self.environment(workspace)},
        )
        output, tool_uses, events = self.parse(proc.stdout)
        cost, input_tokens, output_tokens = self.usage(events)
        return RunResult(
            harness=self.name,
            prompt=prompt,
            workspace=workspace,
            exit_code=proc.returncode,
            duration_s=time.monotonic() - started,
            output=output,
            tool_uses=tool_uses,
            stdout=proc.stdout,
            stderr=proc.stderr,
            events=events,
            model=self.detect_model(events),
            cost_usd=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def run(
        self,
        prompt: str,
        workspace: Path,
        timeout: int,
        answers: list[str] | Mapping[str, str] | Callable[[str], str | None] | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> RunResult:
        """Run one prompt, answering the agent as it hands control back.

        A turn ends when the agent stops and waits. A list of answers is given in
        order, whether or not the message looks like a question; a mapping answers
        the question its pattern matches; a callable is asked what to say. What the
        agent said at each hand-back is kept in `handbacks`.
        """
        self.prepare(workspace)
        # Each turn carries the workspace as it was when that turn began, so
        # `turn(1).created()` is what the agent wrote after the user replied.
        before = snapshot(workspace)
        turn = self._turn(self.command(prompt, workspace), prompt, workspace, timeout)
        turn.before, turn.after = before, snapshot(workspace)
        turns = [turn]

        replies = Replies(answers)
        cap = replies.limit(max_turns)
        while len(turns) < cap:
            answer = replies.next(turn.output)
            if answer is None:
                break
            session = self.session_id(turn.events)
            if not session:
                raise RuntimeError(f"{self.name} reported no session id to resume")
            before = snapshot(workspace)
            turn = self._turn(
                self.resume_command(answer, workspace, session), answer, workspace, timeout
            )
            turn.before, turn.after = before, snapshot(workspace)
            turns.append(turn)

        return merge_turns(turns)


class ClaudeHarness(Harness):
    name = "claude"
    binary = "claude"
    skill_dirs = (".claude/skills",)
    agent_dirs = (".claude/agents",)
    isolated = True

    def isolate(self, root: Path) -> dict[str, str]:
        directory = config_dir(root, "claude-config", ["~/.claude/.credentials.json"])
        return {"CLAUDE_CONFIG_DIR": str(directory)}

    def command(self, prompt: str, workspace: Path) -> list[str]:
        # Sessions persist so a run can be resumed to answer a question. They land
        # in the throwaway config directory `isolate` sets up, not the real one.
        command = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if self.model:
            command += ["--model", self.model]
        return command

    def resume_command(self, prompt: str, workspace: Path, session: str) -> list[str]:
        return self.command(prompt, workspace) + ["--resume", session]

    def session_id(self, events: list[dict]) -> str | None:
        return find_value(events, ("session_id",))

    def detect_model(self, events: list[dict]) -> str:
        for event in events:
            if event.get("type") == "system" and event.get("subtype") == "init":
                return event.get("model") or super().detect_model(events)
        return super().detect_model(events)

    def usage(self, events: list[dict]) -> tuple[float | None, int, int]:
        cost, read, written = None, 0, 0
        for event in events:
            if event.get("type") != "result":
                continue
            if isinstance(event.get("total_cost_usd"), (int, float)):
                cost = (cost or 0) + event["total_cost_usd"]
            counts = event.get("usage") or {}
            read += sum(
                counts.get(key, 0) or 0
                for key in (
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                )
            )
            written += counts.get("output_tokens", 0) or 0
        return cost, read, written

    def parse(self, stdout: str) -> tuple[str, list[ToolUse], list[dict]]:
        events = json_lines(stdout)
        output = ""
        tool_uses = []
        for event in events:
            if event.get("type") == "result":
                output = event.get("result", "") or ""
            if event.get("type") != "assistant":
                continue
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tool_uses.append(ToolUse(block["name"], block.get("input", {})))
        return output, tool_uses, events


class CodexHarness(Harness):
    name = "codex"
    binary = "codex"
    skill_dirs = (".agents/skills",)
    # Not a codex convention: codex has no directory of named subagents, so the
    # definition is dropped in the workspace and the prompt points the model at it.
    agent_dirs = (".agents/agents",)
    names_agents = False
    isolated = True

    def isolate(self, root: Path) -> dict[str, str]:
        return {"CODEX_HOME": str(config_dir(root, "codex-home", ["~/.codex/auth.json"]))}

    def agent_reference(self, agent: str) -> str:
        return f"a subagent whose instructions are the contents of .agents/agents/{agent}.md"

    def command(self, prompt: str, workspace: Path) -> list[str]:
        command = [
            "codex",
            "exec",
            "--cd",
            str(workspace),
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
        ]
        if self.model:
            command += ["--model", self.model]
        return command + [prompt]

    def resume_command(self, prompt: str, workspace: Path, session: str) -> list[str]:
        # `exec resume` takes no --cd, so the subprocess cwd carries the workspace.
        bypass = "--dangerously-bypass-approvals-and-sandbox"
        command = ["codex", "exec", "resume", bypass, "--json"]
        if self.model:
            command += ["--model", self.model]
        return command + [session, prompt]

    def session_id(self, events: list[dict]) -> str | None:
        return find_value(events, ("thread_id",))

    def usage(self, events: list[dict]) -> tuple[float | None, int, int]:
        # codex reports tokens per completed turn and no price.
        read = written = 0
        for event in events:
            counts = event.get("usage") if isinstance(event.get("usage"), dict) else None
            if not counts:
                continue
            read += (counts.get("input_tokens", 0) or 0) + (
                counts.get("cached_input_tokens", 0) or 0
            )
            written += counts.get("output_tokens", 0) or 0
        return None, read, written

    def parse(self, stdout: str) -> tuple[str, list[ToolUse], list[dict]]:
        events = json_lines(stdout)
        output = ""
        tool_uses = []
        for event in events:
            if event.get("type") != "item.completed":
                continue
            item = event.get("item", {})
            kind = item.get("type", "")
            if kind == "agent_message":
                output = item.get("text", "") or output
            elif kind == "command_execution":
                tool_uses.append(ToolUse("Bash", {"command": item.get("command", "")}))
            elif kind == "file_change":
                tool_uses.append(ToolUse("Edit", {"changes": item.get("changes", [])}))
            elif kind == "collab_tool_call":
                # codex spawns unnamed subagents: there is no subagent_type to record.
                tool_uses.append(
                    ToolUse("collab", {"tool": item.get("tool"), "prompt": item.get("prompt")})
                )
        return output, tool_uses, events


class OpencodeHarness(Harness):
    name = "opencode"
    binary = "opencode"
    skill_dirs = (".agents/skills", ".opencode/skills")
    agent_dirs = (".opencode/agent",)

    # No isolation: opencode keeps auth across its config and data directories with
    # no single credential file to link, so redirecting them logs the run out.
    # Globally installed skills stay visible to an opencode run.

    def prepare(self, workspace: Path) -> None:
        """Approve everything for the run, the way the other CLIs are told to.

        opencode has no bypass flag: permissions come from config, and anything
        left on "ask" is refused outright in non-interactive `run` mode. The
        default asks before touching directories outside the project, which is
        most of what a skill working in /tmp does.
        """
        config = workspace / "opencode.json"
        if config.exists():
            return
        config.write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "permission": {
                        "*": "allow",
                        "bash": "allow",
                        "edit": "allow",
                        "webfetch": "allow",
                        "external_directory": "allow",
                    },
                },
                indent=2,
            )
            + "\n"
        )

    def render_agent(self, definition: str) -> str:
        """Rewrite a Claude subagent definition into opencode's frontmatter.

        opencode needs `mode: subagent` to expose the agent to the task tool, has
        no `name` key (the filename names the agent), and spells `tools` as a map
        rather than a list, so the tool list is dropped and the agent runs with
        the default set.
        """
        lines = definition.splitlines()
        if lines[:1] != ["---"] or "---" not in lines[1:]:
            return definition
        end = lines.index("---", 1)

        kept, dropping = [], False
        for line in lines[1:end]:
            if re.match(r"^[A-Za-z][\w-]*:", line):
                dropping = line.split(":", 1)[0] in ("name", "tools", "mode")
            if not dropping:
                kept.append(line)
        return "\n".join(["---", *kept, "mode: subagent", "---", *lines[end + 1 :]]) + "\n"

    def command(self, prompt: str, workspace: Path) -> list[str]:
        command = ["opencode", "run", "--format", "json", "--dir", str(workspace)]
        if self.model:
            command += ["--model", self.model]
        return command + [prompt]

    def resume_command(self, prompt: str, workspace: Path, session: str) -> list[str]:
        return self.command(prompt, workspace) + ["--session", session]

    def session_id(self, events: list[dict]) -> str | None:
        return find_value(events, ("sessionID",))

    def detect_model(self, events: list[dict]) -> str:
        return find_value(events, ("modelID", "model")) or super().detect_model(events)

    def usage(self, events: list[dict]) -> tuple[float | None, int, int]:
        """Sum one entry per assistant message.

        opencode streams the same message repeatedly as it grows, so counting
        every event would multiply the totals. Keyed by message id, the last
        version of each message wins.
        """
        seen: dict[str, tuple[float, int, int]] = {}
        for index, event in enumerate(events):
            part = event.get("part", event)
            counts = part.get("tokens")
            if not isinstance(counts, dict):
                continue
            cache = counts.get("cache") or {}
            key = str(part.get("messageID") or part.get("id") or index)
            seen[key] = (
                part.get("cost", 0) or 0,
                (counts.get("input", 0) or 0) + sum(v or 0 for v in cache.values()),
                counts.get("output", 0) or 0,
            )
        if not seen:
            return None, 0, 0
        totals = list(zip(*seen.values(), strict=True))
        return sum(totals[0]) or None, sum(totals[1]), sum(totals[2])

    def parse(self, stdout: str) -> tuple[str, list[ToolUse], list[dict]]:
        events = json_lines(stdout)
        output = ""
        tool_uses = []
        for event in events:
            part = event.get("part", event)
            if part.get("type") == "text":
                output = part.get("text", "") or output
            elif part.get("type") == "tool":
                state = part.get("state", {})
                tool_uses.append(ToolUse(part.get("tool", "tool"), state.get("input", {})))
        return output or stdout.strip(), tool_uses, events


class DroidHarness(Harness):
    name = "droid"
    binary = "droid"
    skill_dirs = (".factory/skills",)
    # droid's own agents are "droids", and its Task tool does not take a name a
    # test could assert on, so a definition has nowhere useful to go. `delegated`
    # still reports that work was handed off.
    agent_dirs = ()
    names_agents = False

    # No isolation: FACTORY_HOME_OVERRIDE points droid at a throwaway directory,
    # but its credentials do not survive being moved there -- copied or symlinked,
    # the CLI reports "authentication failed". Globally installed skills stay
    # visible to a droid run.
    isolated = False

    def command(self, prompt: str, workspace: Path) -> list[str]:
        command = [
            "droid",
            "exec",
            "-o",
            "stream-json",
            "--skip-permissions-unsafe",
            "--cwd",
            str(workspace),
        ]
        if self.model:
            command += ["-m", self.model]
        return command + [prompt]

    def resume_command(self, prompt: str, workspace: Path, session: str) -> list[str]:
        command = self.command(prompt, workspace)
        return command[:-1] + ["-s", session, command[-1]]

    def session_id(self, events: list[dict]) -> str | None:
        return find_value(events, ("session_id",))

    def detect_model(self, events: list[dict]) -> str:
        for event in events:
            if event.get("type") == "system" and event.get("subtype") == "init":
                return event.get("model") or super().detect_model(events)
        return super().detect_model(events)

    def usage(self, events: list[dict]) -> tuple[float | None, int, int]:
        # droid prices in its own credits, not dollars, so no cost is reported.
        read = written = 0
        for event in events:
            if event.get("type") != "completion":
                continue
            counts = event.get("usage") or {}
            read += sum(
                counts.get(key, 0) or 0
                for key in (
                    "input_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            )
            written += counts.get("output_tokens", 0) or 0
        return None, read, written

    def parse(self, stdout: str) -> tuple[str, list[ToolUse], list[dict]]:
        events = json_lines(stdout)
        output = ""
        tool_uses = []
        for event in events:
            kind = event.get("type")
            if kind == "completion":
                output = event.get("finalText", "") or output
            elif kind == "message" and event.get("role") == "assistant":
                output = event.get("text", "") or output
            elif kind == "tool_call":
                tool_uses.append(
                    ToolUse(
                        event.get("toolName") or event.get("toolId", "tool"),
                        event.get("parameters", {}),
                    )
                )
        return output, tool_uses, events


HARNESSES: dict[str, type[Harness]] = {
    harness.name: harness
    for harness in (ClaudeHarness, CodexHarness, OpencodeHarness, DroidHarness)
}


def get_harness(name: str, model: str | None = None, container: str | None = None) -> Harness:
    if name not in HARNESSES:
        raise KeyError(f"unknown harness {name!r}; pick one of {sorted(HARNESSES)}")
    return HARNESSES[name](model=model, container=container)
