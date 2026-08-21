"""The fake-binary machinery, which is what keeps a test away from real services."""

import json
import os
import subprocess

import pytest

from skillcheck import fakebin
from skillcheck.harnesses import FAKE_BIN, FAKE_STATE, ClaudeHarness


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def run_gh(workspace, *args):
    harness = ClaudeHarness()
    return subprocess.run(
        [str(workspace / FAKE_BIN / "gh"), *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        env={**os.environ, **harness.environment(workspace)},
    )


def test_install_puts_an_executable_stub_and_its_fixture_in_place(workspace):
    fakebin.install(workspace, "gh", {"me/proj": {"visibility": "PUBLIC"}})

    stub = workspace / FAKE_BIN / "gh"
    assert stub.is_file() and os.access(stub, os.X_OK)
    fixture = json.loads((workspace / FAKE_STATE / "gh" / "fixture.json").read_text())
    assert fixture["me/proj"]["visibility"] == "PUBLIC"


def test_the_stub_answers_the_marker_and_the_commands_it_knows(workspace):
    fakebin.install(workspace, "gh", {"me/proj": {"visibility": "PUBLIC", "contents": ["old"]}})

    assert fakebin.MARKER in run_gh(workspace, "--skillcheck-fake").stdout
    view = run_gh(workspace, "repo", "view", "me/proj", "--json", "visibility", "-q", ".visibility")
    assert view.stdout.strip() == "PUBLIC"
    listing = run_gh(workspace, "api", "repos/me/proj/contents", "-q", ".[].name")
    assert listing.stdout.strip() == "old"


def test_the_stub_refuses_anything_the_test_did_not_set_up(workspace):
    fakebin.install(workspace, "gh", {"me/proj": {"visibility": "PUBLIC"}})

    refused = run_gh(workspace, "pr", "create", "--title", "x")
    assert refused.returncode == 64
    assert "unsupported command" in refused.stderr

    unknown = run_gh(workspace, "repo", "view", "someone/else")
    assert unknown.returncode == 1


def test_delete_is_recorded_and_needs_confirmation(workspace):
    fakebin.install(workspace, "gh", {"me/proj": {"visibility": "PUBLIC"}})

    assert run_gh(workspace, "repo", "delete", "me/proj").returncode == 64
    assert run_gh(workspace, "repo", "delete", "me/proj", "--yes").returncode == 0
    # A deleted repo stops existing, the way it would on the real thing.
    assert run_gh(workspace, "repo", "view", "me/proj").returncode == 1


def test_calls_are_logged_for_a_test_to_assert_on(workspace, tmp_path):
    from skillcheck.harnesses import RunResult

    fakebin.install(workspace, "gh", {"me/proj": {"visibility": "PUBLIC"}})
    run_gh(workspace, "repo", "view", "me/proj")
    run_gh(workspace, "pr", "create")

    result = RunResult("claude", "p", workspace, 0, 1.0, "", [], "", "")
    assert result.called("gh", "repo", "view", "me/proj")
    assert not result.called("gh", "repo", "delete", "me/proj")
    assert result.refusals("gh") == [["pr", "create"]]


def test_verify_refuses_when_path_does_not_resolve_to_the_stub(workspace):
    with pytest.raises(RuntimeError, match="not the skillcheck fake"):
        fakebin.verify(ClaudeHarness(), workspace, "gh")


def test_git_is_blocked_from_reaching_a_real_forge(workspace):
    fakebin.install(workspace, "gh", {})
    harness = ClaudeHarness()

    # The rewrite applies at transport time, so ask git to reach github and watch
    # it resolve to the blocked path instead.
    reach = subprocess.run(
        ["git", "ls-remote", "https://github.com/someone/private-thing"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, **harness.environment(workspace)},
    )
    assert reach.returncode != 0
    assert "blocked-by-skillcheck" in reach.stderr

    identity = subprocess.run(
        ["git", "config", "user.email"],
        cwd=workspace,
        capture_output=True,
        text=True,
        env={**os.environ, **harness.environment(workspace)},
    )
    assert identity.stdout.strip().endswith("example.invalid")


def test_asking_for_a_fake_that_does_not_exist_says_what_ships(workspace):
    with pytest.raises(FileNotFoundError, match="skillcheck ships"):
        fakebin.install(workspace, "kubectl", {})
