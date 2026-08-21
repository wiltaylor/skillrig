# repo-docs audit

Standard: `/home/wil/.claude/skills/repo-docs/reference/standard.md`
Config: none — shipped default
Run: 2026-08-21

| Finding | Severity | Status | Detail |
|---------|----------|--------|--------|
| `README.md/missing-section/usage` | error | applied | Usage section written after Install |
| `README.md/missing-section/contributing` | error | applied | Contributing section written after Usage |
| `README.md/missing-section/toc` | warn | applied | Contents list written after the intro, before Install |
| `README.md/missing-badge/ci-badge` | warn | applied | CI badge for `.github/workflows/ci.yml` written under the title |
| `README.md/missing-badge/package-badge` | warn | applied | PyPI badge for `pytest-skillcheck` written under the title, publication confirmed by the user |
| `CONTRIBUTING.md/missing-document` | warn | open | `fix` amends documents; writing a missing one is `/repo-docs init` |
| `CODE_OF_CONDUCT.md/missing-document` | warn | open | `fix` amends documents; writing a missing one is `/repo-docs init` |
| `SECURITY.md/missing-document` | warn | open | `fix` amends documents; writing a missing one is `/repo-docs init` |
| `ISSUE_TEMPLATE/missing-document` | warn | open | `fix` amends documents; writing a missing one is `/repo-docs init` |
| `PULL_REQUEST_TEMPLATE/missing-document` | warn | open | `fix` amends documents; writing a missing one is `/repo-docs init` |

23 rules checked after the fix: 0 error, 5 warn.
