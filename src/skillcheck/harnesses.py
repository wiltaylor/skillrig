"""Adapters that run one prompt against one coding agent CLI.

Each adapter turns a prompt into a subprocess invocation and normalises whatever
that CLI streams back into a `RunResult`, so a test asserts against the same
shape no matter which agent produced it.
"""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

# Workspace-relative directories and files that belong to a harness, not a test.
HARNESS_DIRS = {".claude", ".agents", ".codex", ".opencode", ".git", ".skillcheck"}
HARNESS_FILES = {"opencode.json"}

# Where fake binaries and their state live inside the workspace.
FAKE_BIN = ".skillcheck/bin"
FAKE_STATE = ".skillcheck/state"


@dataclass
class ToolUse:
    name: str
    input: dict


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

    def transcript(self) -> str:
        """Prompt, tool calls, and final answer, as text for a judge."""
        lines = [f"# Prompt\n{self.prompt}\n"]
        for index, question in enumerate(self.questions, start=1):
            lines.append(f"# Question {index}\n{question}\n")
        lines.append("# Tool calls")
        for use in self.tool_uses:
            lines.append(f"- {use.name}: {json.dumps(use.input)[:600]}")
        lines.append(f"\n# Final answer\n{self.output}")
        return "\n".join(lines)


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
    )


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

    def __init__(self, model: str | None = None):
        self.model = model or self.default_model

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

    def _turn(self, command: list[str], prompt: str, workspace: Path, timeout: int) -> RunResult:
        started = time.monotonic()
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
        )

    def run(
        self, prompt: str, workspace: Path, timeout: int, answers: list[str] | None = None
    ) -> RunResult:
        """Run one prompt, replying with `answers` in order as the agent hands back.

        A turn ends when the agent stops and waits, so that is where the next
        answer goes, whether or not the message looks like a question. What the
        agent said at each of those points is kept in `handbacks`.
        """
        self.prepare(workspace)
        turn = self._turn(self.command(prompt, workspace), prompt, workspace, timeout)
        turns = [turn]

        for answer in answers or []:
            session = self.session_id(turn.events)
            if not session:
                raise RuntimeError(f"{self.name} reported no session id to resume")
            turn = self._turn(
                self.resume_command(answer, workspace, session), answer, workspace, timeout
            )
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


HARNESSES: dict[str, type[Harness]] = {
    harness.name: harness for harness in (ClaudeHarness, CodexHarness, OpencodeHarness)
}


def get_harness(name: str, model: str | None = None) -> Harness:
    if name not in HARNESSES:
        raise KeyError(f"unknown harness {name!r}; pick one of {sorted(HARNESSES)}")
    return HARNESSES[name](model=model)
