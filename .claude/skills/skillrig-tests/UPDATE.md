# Update: skillrig-tests

<purpose>
This skill mirrors skillrig's own API: the fixtures `run_skill` and `judge` accept,
every member of `RunResult`, the fixture format the `gh` fake reads, the
`SKILLRIG_*` settings, and the `skillrig` subcommands. All of it lives in this
repository, so the skill goes stale the moment a signature changes here. Up to
date means every call the reference files show exists at the current commit, and
nothing added since is missing.
</purpose>

<sources>
This repository is the source of truth. Read the code before the README — the
README is documentation of the same thing and can lag it.

- `src/skillrig/harnesses.py` — the `RunResult` dataclass and its methods, the
  `Harness` subclasses, and `HARNESSES`. Feeds `reference/assertions.md` and the
  harness table in `reference/running.md`
- `src/skillrig/plugin.py` — the fixtures and the `run_agent` signature
  (`skill`, `agent`, `files`, `answers`, `fake`, `timeout`), plus the pytest
  options. Feeds `SKILL.md` step 3 and `reference/running.md`
- `src/skillrig/fakebin.py` and `src/skillrig/fakes/gh.py` — `install`, the four
  PATH guards, the fixture and `calls.jsonl` formats. Feeds `reference/fakes.md`
- `src/skillrig/config.py` — the `SKILLRIG_*` variables and their defaults. Feeds
  the settings table in `reference/running.md`
- `src/skillrig/cli.py` — the `skillrig` subcommands and the `new-test` template.
  Feeds the CLI section of `reference/running.md`
- `src/skillrig/judge.py` — `judge`, `Verdict`, and the backends
- `README.md` — cross-check only, for anything the code leaves ambiguous
- `git log --oneline -20 -- src/skillrig/` — what changed since the last update
</sources>

<questions>
| ID | Question | Default |
|----|----------|---------|
| Q1 | Pin `dependencies = ["skillrig"]` in the canonical test file to a minimum version? | No pin. The published package is this repo, and a floor goes stale on its own |
| Q2 | Document a harness whose CLI nobody here has installed? | Yes. The table describes what skillrig supports, not what is on this machine |
</questions>

<procedure>
<step order="1">
Run `git log --oneline -20 -- src/skillrig/` and note which modules changed since
the reference files were last touched. Done when you have the list of modules to
re-read.
</step>

<step order="2">
Re-read `harnesses.py` and compare every `RunResult` field, property, and method
against the two tables in `reference/assertions.md`. Add what is new, delete what
no longer exists, and correct any changed semantics. Done when the tables and the
dataclass agree member for member.
</step>

<step order="3">
Re-read `plugin.py` and check the `run` signature in `run_agent` against the
argument list in `SKILL.md` step 3, and the pytest options against
`reference/running.md`. Done when both match.
</step>

<step order="4">
Re-read `fakebin.py` and `fakes/gh.py`. Check the four guards in
`reference/fakes.md` still describe what the code does, and that the fixture and
log formats are right. Done when a reader could write a new fake from the section
and have it pass `verify`.
</step>

<step order="5">
Re-read `config.py` and `cli.py`. Update the settings table and the CLI block in
`reference/running.md`, including defaults. Done when every variable and
subcommand is listed with its current default.
</step>

<step order="6">
Check `HARNESSES` for a harness added since, and add a row to the table in
`reference/running.md` with its skill directories and whether it isolates. Done
when the table lists every registered harness.
</step>
</procedure>

<verification>
Every path this skill names must exist: `ls` each file listed in `<sources>`.
Every method named in `reference/assertions.md` must appear in `harnesses.py`.

The skill's own tests are `./.claude/skills/skillrig-tests/test.py`. Running them
spends real model calls, so that is the user's call — say so rather than running
them unasked.
</verification>

<out-of-scope>
An update refreshes content only. It does not restructure the skill, change its
`description` or `user-invocable` setting, add subcommands, or rewrite the
boundaries. Those are `/meta-skill audit` work.

skillrig's own design — which assertions should exist, whether a harness belongs —
is not an update either. Changing the library is a change to this repository, not
to the skill that documents it.
</out-of-scope>
