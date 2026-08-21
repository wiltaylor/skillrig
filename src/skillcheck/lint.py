"""Checks a skill can fail without a model being involved.

The cheapest test in the suite: no credentials, no cost, no flakiness. Most of
what breaks a skill in practice is here -- a description an agent will never
match, a name that does not agree with the directory, a link to a reference file
that was renamed.

Every finding has a stable code, so a project can decide which ones it cares
about without the messages shifting under it.
"""

import re
from dataclasses import dataclass
from pathlib import Path

# The longest description a skill front-matter is accepted with by the CLIs that
# read one, and the longest name.
MAX_DESCRIPTION = 1024
MAX_NAME = 64
LONG_BODY_LINES = 500

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    path: Path

    def __str__(self) -> str:
        return f"{self.path}: {self.severity} {self.code} {self.message}"


def parse_frontmatter(text: str) -> dict[str, str]:
    """The `key: value` block at the top of a SKILL.md.

    Deliberately small: skill front-matter is flat, and depending on a YAML
    library for it would put a dependency in everyone's test environment.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}

    fields: dict[str, str] = {}
    current = None
    for line in lines[1:end]:
        match = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if match:
            current = match.group(1)
            fields[current] = match.group(2).strip().strip("'\"")
        elif current and line.strip():
            fields[current] = f"{fields[current]} {line.strip()}".strip()
    return fields


def body_of(text: str) -> str:
    lines = text.splitlines()
    if lines[:1] == ["---"] and "---" in lines[1:]:
        return "\n".join(lines[lines.index("---", 1) + 1 :])
    return text


LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def links(text: str) -> list[str]:
    """Relative link targets in a markdown body, external ones dropped."""
    found = []
    for target in LINK.findall(text):
        target = target.split("#")[0].split(" ")[0].strip()
        if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("/"):
            continue
        found.append(target)
    return found


def lint_skill(directory: Path) -> list[Finding]:
    """Every finding for one skill directory."""
    directory = Path(directory)
    skill_file = directory / "SKILL.md"
    findings: list[Finding] = []

    def report(code, severity, message, path=skill_file):
        findings.append(Finding(code, severity, message, path))

    if not skill_file.is_file():
        report("SK001", ERROR, "no SKILL.md in this directory", directory)
        return findings

    text = skill_file.read_text()
    fields = parse_frontmatter(text)
    if not fields:
        report("SK002", ERROR, "no front-matter: the file must open with a --- block")
        return findings

    name = fields.get("name", "")
    description = fields.get("description", "")

    if not name:
        report("SK003", ERROR, "front-matter has no name")
    if not description:
        report("SK004", ERROR, "front-matter has no description, so nothing can trigger the skill")

    if name and name != directory.name:
        report("SK005", ERROR, f"name is {name!r} but the directory is {directory.name!r}")
    if name and not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        report("SK006", ERROR, f"name {name!r} is not lowercase-and-hyphens")
    if len(name) > MAX_NAME:
        report("SK006", ERROR, f"name is {len(name)} characters, over the {MAX_NAME} limit")

    if len(description) > MAX_DESCRIPTION:
        report(
            "SK007",
            ERROR,
            f"description is {len(description)} characters, over the {MAX_DESCRIPTION} limit",
        )
    if (
        description
        and "use when" not in description.lower()
        and "use it when" not in description.lower()
    ):
        report(
            "SK008",
            WARNING,
            "description does not say when to use the skill, which is what an agent matches on",
        )

    body = body_of(text)
    for target in links(body):
        if not (directory / target).exists():
            report("SK009", ERROR, f"link target does not exist: {target}")

    if len(body.splitlines()) > LONG_BODY_LINES:
        report(
            "SK010",
            WARNING,
            f"SKILL.md is {len(body.splitlines())} lines; move detail into reference files",
        )

    if not (directory / "test.py").is_file():
        report("SK011", WARNING, "no test.py beside the skill", directory)

    return findings


def find_skills(root: Path) -> list[Path]:
    """Every skill directory at or under `root`."""
    root = Path(root)
    if (root / "SKILL.md").is_file():
        return [root]
    return sorted({file.parent for file in root.rglob("SKILL.md")})


def lint(roots: list[Path], strict: bool = False) -> list[Finding]:
    """Findings for every skill under these paths, worst first per skill."""
    findings = []
    for root in roots:
        for directory in find_skills(Path(root)):
            findings.extend(lint_skill(directory))
    if strict:
        findings = [
            Finding(f.code, ERROR, f.message, f.path) if f.severity == WARNING else f
            for f in findings
        ]
    return findings


def failed(findings: list[Finding]) -> bool:
    return any(finding.severity == ERROR for finding in findings)
