"""The fakes a test describes rather than writes: the generic engine, and curl.

Each one runs as a real subprocess, because that is how an agent invokes it.
"""

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


def run(workspace, binary, *args):
    harness = ClaudeHarness()
    return subprocess.run(
        [str(workspace / FAKE_BIN / binary), *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        env={**os.environ, **harness.environment(workspace)},
    )


def calls(workspace, binary):
    log = workspace / FAKE_STATE / binary / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


# -- the generic engine ----------------------------------------------------


def test_a_command_map_answers_what_it_describes(workspace):
    fakebin.install(
        workspace,
        "kubectl",
        {
            "get pods": "NAME   READY\nweb-0  1/1",
            "delete .*": {"stderr": "forbidden", "exit": 1},
        },
    )

    assert fakebin.MARKER in run(workspace, "kubectl", "--skillcheck-fake").stdout

    listed = run(workspace, "kubectl", "get", "pods")
    assert listed.returncode == 0
    assert "web-0" in listed.stdout

    denied = run(workspace, "kubectl", "delete", "pod", "web-0")
    assert denied.returncode == 1
    assert "forbidden" in denied.stderr


def test_it_refuses_a_command_the_fixture_never_described(workspace):
    fakebin.install(workspace, "kubectl", {"get pods": "none"})

    refused = run(workspace, "kubectl", "apply", "-f", "deploy.yaml")
    assert refused.returncode == 64
    assert "no fixture matches" in refused.stderr
    assert calls(workspace, "kubectl")[-1]["status"] == "refused"


def test_a_list_of_responses_is_used_one_call_at_a_time(workspace):
    fakebin.install(
        workspace,
        "terraform",
        {"commands": {"apply": ["in progress", "in progress", "complete"]}},
    )

    assert run(workspace, "terraform", "apply").stdout.strip() == "in progress"
    assert run(workspace, "terraform", "apply").stdout.strip() == "in progress"
    assert run(workspace, "terraform", "apply").stdout.strip() == "complete"
    # The last entry answers every call after it, rather than running out.
    assert run(workspace, "terraform", "apply").stdout.strip() == "complete"


def test_a_response_can_write_the_file_the_real_command_would_have(workspace):
    fakebin.install(
        workspace,
        "openssl",
        {"req .*": {"stdout": "done", "writes": {"certs/key.pem": "PRIVATE KEY"}}},
    )

    run(workspace, "openssl", "req", "-new")
    assert (workspace / "certs/key.pem").read_text() == "PRIVATE KEY"


def test_every_call_is_logged_in_order_for_the_test_to_assert_on(workspace):
    fakebin.install(workspace, "docker", {"build .*": "built", "push .*": "pushed"})

    run(workspace, "docker", "build", "-t", "app", ".")
    run(workspace, "docker", "push", "app")

    assert [call["argv"][0] for call in calls(workspace, "docker")] == ["build", "push"]
    assert all(call["status"] == "ok" for call in calls(workspace, "docker"))


# -- curl ------------------------------------------------------------------


def test_curl_answers_the_urls_the_fixture_describes(workspace):
    fakebin.install(
        workspace,
        "curl",
        {
            "https://api.example.com/repos/.*": {"body": '{"name": "proj"}'},
            "https://example.com/missing": {"status": 404, "body": "not found"},
        },
    )

    assert fakebin.MARKER in run(workspace, "curl", "--skillcheck-fake").stdout

    fetched = run(workspace, "curl", "-s", "https://api.example.com/repos/me/proj")
    assert json.loads(fetched.stdout)["name"] == "proj"

    missing = run(workspace, "curl", "-s", "-f", "https://example.com/missing")
    assert missing.returncode == 22


def test_curl_writes_to_the_output_file_and_reports_the_status(workspace):
    fakebin.install(workspace, "curl", {"https://example.com/thing": {"body": "payload"}})

    result = run(
        workspace,
        "curl",
        "-sSL",
        "-o",
        "downloaded.txt",
        "-w",
        "%{http_code}",
        "https://example.com/thing",
    )

    assert (workspace / "downloaded.txt").read_text() == "payload"
    assert result.stdout.strip() == "200"


def test_curl_refuses_a_url_the_test_did_not_set_up(workspace):
    fakebin.install(workspace, "curl", {"https://example.com/thing": "ok"})

    refused = run(workspace, "curl", "-s", "https://evil.example.net/steal")
    assert refused.returncode == 64
    assert "no fixture matches the URL" in refused.stderr


def test_curl_does_not_mistake_a_header_value_for_the_url(workspace):
    fakebin.install(workspace, "curl", {"https://example.com/api": "ok"})

    result = run(
        workspace,
        "curl",
        "-X",
        "POST",
        "-H",
        "Accept: application/json",
        "-d",
        "name=value",
        "https://example.com/api",
    )
    assert result.returncode == 0
    assert result.stdout == "ok"
