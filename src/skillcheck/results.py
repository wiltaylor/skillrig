"""Results, recorded next to the test that produced them.

A skill's results.json sits beside its test file, so the record of what the skill
was tested on travels with the skill. Someone who runs it against a harness you
do not have can send the result back as a pull request, and an issue can point at
the row that failed.

Runs merge by test id, so a narrow run only updates what it ran and every other
row keeps its previous result and date.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

FILENAME = "results.json"


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def path_for(test_file: Path, override: str | None = None) -> Path:
    """Where results for a given test file belong."""
    return Path(override) if override else Path(test_file).parent / FILENAME


def load(path: Path) -> dict:
    if not Path(path).is_file():
        return {}
    try:
        return json.loads(Path(path).read_text())
    except json.JSONDecodeError:
        return {}


def merge(path: Path, skill: str, records: dict[str, dict]) -> None:
    """Fold this run's records into the file, leaving untouched rows alone."""
    stored = load(path)
    runs = stored.get("runs", {})
    for test_id, record in records.items():
        previous = runs.get(test_id, {})
        if record.get("outcome") == "skipped" and previous.get("outcome") in ("passed", "failed"):
            # A skip says nothing about the skill, so keep the last real result.
            continue
        # Replaced, not merged: a run that failed before reaching its judge would
        # otherwise keep the previous run's score and look like it still passed.
        runs[test_id] = record
    stored["skill"] = skill or stored.get("skill", "")
    stored["runs"] = dict(sorted(runs.items()))
    Path(path).write_text(json.dumps(stored, indent=2) + "\n")


def age(stamp: str | None) -> str:
    if not stamp:
        return "never"
    ran = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    days = (datetime.now(UTC) - ran).days
    if days == 0:
        return "today"
    return f"{days} day{'s' if days > 1 else ''} ago"


def collect(roots: list[Path]) -> list[dict]:
    """Every recorded run under these paths, one row per skill and harness."""
    rows = []
    for root in roots:
        root = Path(root)
        files = [root] if root.is_file() else sorted(root.rglob(FILENAME))
        for file in files:
            stored = load(file)
            skill = stored.get("skill") or file.parent.parent.name
            for test_id, record in stored.get("runs", {}).items():
                rows.append({"skill": skill, "test": test_id, **record})
    return rows


STATUS = {"passed": "pass", "failed": "FAIL", "skipped": "n/a"}


def summarise(rows: list[dict]) -> tuple[list[str], list[list[str]]]:
    """One row per skill, one column group per harness."""
    harnesses = sorted({row.get("harness", "?") for row in rows})
    skills = sorted({row["skill"] for row in rows})

    table = []
    for skill in skills:
        line = [skill]
        for harness in harnesses:
            group = [r for r in rows if r["skill"] == skill and r.get("harness", "?") == harness]
            if not group:
                line += ["-", "-", "-"]
                continue
            outcomes = [row.get("outcome") for row in group]
            if all(outcome == "skipped" for outcome in outcomes):
                line += ["n/a", "-", "-"]
                continue
            status = "FAIL" if "failed" in outcomes else "pass"
            stamps = [row.get("ran_at") for row in group if row.get("ran_at")]
            seconds = sum(row.get("duration_s", 0) for row in group)
            line += [status, age(max(stamps) if stamps else None), f"{seconds:.0f}s"]
        table.append(line)
    return harnesses, table


def report(rows: list[dict]) -> str:
    """Pass rate, time, and cost per skill and harness.

    `status` answers "is it green". This answers "what is it costing, and how
    often does it really pass", which is what decides whether a suite can run in
    CI at all.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["skill"], row.get("harness", "?")), []).append(row)

    table = []
    for (skill, harness), group in sorted(groups.items()):
        outcomes = [row.get("outcome") for row in group]
        ran = [outcome for outcome in outcomes if outcome in ("passed", "failed")]
        passed = outcomes.count("passed")
        rate = f"{passed / len(ran) * 100:.0f}%" if ran else "-"
        cost = sum(row.get("cost_usd", 0) or 0 for row in group)
        tokens = sum(row.get("tokens", 0) or 0 for row in group)
        stamps = [row.get("ran_at") for row in group if row.get("ran_at")]
        table.append(
            [
                skill,
                harness,
                str(len(group)),
                f"{passed}/{len(ran)}" if ran else "0/0",
                rate,
                f"{sum(row.get('duration_s', 0) for row in group):.0f}s",
                f"${cost:.2f}" if cost else "-",
                f"{tokens / 1000:.0f}k" if tokens else "-",
                age(max(stamps) if stamps else None),
            ]
        )

    headers = ["SKILL", "HARNESS", "TESTS", "PASSED", "RATE", "TIME", "COST", "TOKENS", "LAST RUN"]
    widths = [max(len(row[i]) for row in [headers, *table]) for i in range(len(headers))]

    def line(cells: list[str]) -> str:
        return "  ".join(
            cell.ljust(width) for cell, width in zip(cells, widths, strict=True)
        ).rstrip()

    return "\n".join(
        [line(headers), "  ".join("-" * width for width in widths), *(line(row) for row in table)]
    )


def render(harnesses: list[str], table: list[list[str]]) -> str:
    headers = ["SKILL"] + ["STATUS", "LAST TESTED", "TIME"] * len(harnesses)
    widths = [max(len(row[i]) for row in [headers, *table]) for i in range(len(headers))]

    for index, harness in enumerate(harnesses):
        first = 1 + index * 3
        span = sum(widths[first : first + 3]) + 4
        if span < len(harness):
            widths[first + 2] += len(harness) - span

    def line(cells: list[str]) -> str:
        pairs = zip(cells, widths, strict=False)
        return "  ".join(cell.ljust(width) for cell, width in pairs).rstrip()

    group = [" " * widths[0]]
    for index, harness in enumerate(harnesses):
        first = 1 + index * 3
        group.append(harness.ljust(sum(widths[first : first + 3]) + 4))

    return "\n".join(
        [
            "  ".join(group).rstrip(),
            line(headers),
            "  ".join("-" * width for width in widths),
            *(line(row) for row in table),
        ]
    )
