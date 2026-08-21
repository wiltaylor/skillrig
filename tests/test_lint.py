"""The checks that need no model, and so run on every commit."""

from skillcheck import lint

GOOD = """\
---
name: git-graveyard
description: Archive abandoned repositories. Use when the user wants to bury a repo.
---

# git-graveyard

See [the reference](references/detail.md).
"""


def write_skill(root, name, text, with_test=True):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(text)
    if with_test:
        (directory / "test.py").write_text("# tests\n")
    return directory


def codes(findings):
    return [finding.code for finding in findings]


def test_a_well_formed_skill_reports_nothing(tmp_path):
    directory = write_skill(tmp_path, "git-graveyard", GOOD)
    (directory / "references").mkdir()
    (directory / "references/detail.md").write_text("detail\n")

    assert lint.lint_skill(directory) == []


def test_a_directory_with_no_skill_file_is_an_error(tmp_path):
    (tmp_path / "empty").mkdir()
    assert codes(lint.lint_skill(tmp_path / "empty")) == ["SK001"]


def test_front_matter_that_is_missing_or_unterminated_is_an_error(tmp_path):
    plain = write_skill(tmp_path, "plain", "# just a heading\n")
    unterminated = write_skill(tmp_path, "unterminated", "---\nname: unterminated\n")

    assert codes(lint.lint_skill(plain)) == ["SK002"]
    assert codes(lint.lint_skill(unterminated)) == ["SK002"]


def test_a_missing_name_or_description_is_an_error(tmp_path):
    directory = write_skill(tmp_path, "nameless", "---\nother: value\n---\nbody\n")
    assert "SK003" in codes(lint.lint_skill(directory))
    assert "SK004" in codes(lint.lint_skill(directory))


def test_a_name_that_disagrees_with_the_directory_is_an_error(tmp_path):
    directory = write_skill(
        tmp_path, "graveyard", "---\nname: git-graveyard\ndescription: Use when burying.\n---\n"
    )
    assert "SK005" in codes(lint.lint_skill(directory))


def test_a_name_that_is_not_lowercase_and_hyphens_is_an_error(tmp_path):
    directory = write_skill(
        tmp_path, "Git_Graveyard", "---\nname: Git_Graveyard\ndescription: Use when burying.\n---\n"
    )
    assert "SK006" in codes(lint.lint_skill(directory))


def test_a_description_over_the_limit_is_an_error(tmp_path):
    long = "Use when " + "x" * lint.MAX_DESCRIPTION
    directory = write_skill(tmp_path, "wordy", f"---\nname: wordy\ndescription: {long}\n---\n")
    assert "SK007" in codes(lint.lint_skill(directory))


def test_a_description_that_never_says_when_to_use_it_is_a_warning(tmp_path):
    directory = write_skill(
        tmp_path, "vague", "---\nname: vague\ndescription: Does repository things.\n---\n"
    )

    finding = [f for f in lint.lint_skill(directory) if f.code == "SK008"][0]
    assert finding.severity == lint.WARNING


def test_a_link_to_a_file_that_is_not_there_is_an_error(tmp_path):
    directory = write_skill(tmp_path, "git-graveyard", GOOD)
    assert "SK009" in codes(lint.lint_skill(directory))


def test_links_out_to_the_web_and_to_anchors_are_left_alone(tmp_path):
    body = (
        "---\nname: linky\ndescription: Use when linking.\n---\n"
        "[docs](https://example.com/x) [anchor](#section) [mail](mailto:a@b.c)\n"
    )
    directory = write_skill(tmp_path, "linky", body)
    assert codes(lint.lint_skill(directory)) == []


def test_a_very_long_skill_file_is_a_warning(tmp_path):
    body = "\n".join(["line"] * (lint.LONG_BODY_LINES + 10))
    directory = write_skill(
        tmp_path, "long", f"---\nname: long\ndescription: Use when long.\n---\n{body}\n"
    )
    assert "SK010" in codes(lint.lint_skill(directory))


def test_a_skill_with_no_test_beside_it_is_a_warning(tmp_path):
    directory = write_skill(
        tmp_path, "untested", "---\nname: untested\ndescription: Use when untested.\n---\n", False
    )
    assert "SK011" in codes(lint.lint_skill(directory))


def test_front_matter_folded_over_several_lines_is_read_as_one_value():
    fields = lint.parse_frontmatter(
        "---\nname: wrapped\n"
        "description: Use when the description\n  runs onto a second line.\n"
        "---\nbody\n"
    )
    assert fields["description"] == "Use when the description runs onto a second line."


def test_linting_a_tree_finds_every_skill_under_it(tmp_path):
    write_skill(tmp_path / "skills", "one", "---\nname: one\ndescription: Use when one.\n---\n")
    write_skill(tmp_path / "skills", "two", "---\nname: two\ndescription: Use when two.\n---\n")

    assert len(lint.find_skills(tmp_path)) == 2
    assert lint.lint([tmp_path]) == []


def test_strict_turns_every_warning_into_an_error(tmp_path):
    write_skill(tmp_path, "vague", "---\nname: vague\ndescription: Does things.\n---\n", False)

    assert not lint.failed(lint.lint([tmp_path]))
    assert lint.failed(lint.lint([tmp_path], strict=True))
