import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest
import httpx
import respx

import action as action_mod
from docdr.client import DocDrClient, DocDrApiError, DocDrQuotaExceeded, DocFile
from docdr.diff import FileDiff


@respx.mock
def test_send_maintenance_returns_updates():
    respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(
            200,
            json={
                "updates": [{"path": "README.md", "content": "# Updated"}],
                "no_update": False,
            },
        )
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    diffs = [FileDiff(path="src/main.py", diff="+ def foo(): pass")]
    updates = client.send_maintenance(repo="owner/repo", diffs=diffs, doc_files=[])
    assert len(updates) == 1
    assert updates[0].path == "README.md"
    assert updates[0].content == "# Updated"


@respx.mock
def test_send_maintenance_no_update():
    respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(200, json={"updates": [], "no_update": True})
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    updates = client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
    assert updates == []


@respx.mock
def test_send_bootstrap_sends_source_files():
    route = respx.post("http://localhost:8000/v1/bootstrap").mock(
        return_value=httpx.Response(
            200,
            json={
                "updates": [{"path": "README.md", "content": "# Generated"}],
                "no_update": False,
            },
        )
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    source_files = [DocFile(path="main.py", content="def main(): pass")]
    updates = client.send_bootstrap(
        repo="owner/repo",
        tree="main.py",
        manifests=[],
        source_files=source_files,
    )
    assert len(updates) == 1
    sent = json.loads(route.calls[0].request.content)
    assert "source_files" in sent
    assert sent["source_files"] == [{"path": "main.py", "content": "def main(): pass"}]


@respx.mock
def test_send_bootstrap_source_files_defaults_to_empty():
    route = respx.post("http://localhost:8000/v1/bootstrap").mock(
        return_value=httpx.Response(200, json={"updates": [], "no_update": True})
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    client.send_bootstrap(repo="owner/repo", tree="", manifests=[])
    sent = json.loads(route.calls[0].request.content)
    assert sent["source_files"] == []


@respx.mock
def test_send_maintenance_raises_on_403():
    """403 response raises DocDrApiError (not httpx.HTTPStatusError)."""
    respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(403, json={"detail": "Invalid license key"})
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="bad-key")
    with pytest.raises(DocDrApiError) as exc_info:
        client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
    assert exc_info.value.status_code == 403
    assert "Invalid license key" in exc_info.value.detail


# ── VAL-LLM-017: Action displays error codes from backend ────────────────────


@respx.mock
def test_429_quota_exceeded_raises_quota_exception():
    """429 with quota_exceeded detail raises DocDrQuotaExceeded."""
    respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(
            429, json={"detail": {"error": "quota_exceeded", "used": 100, "quota": 100}}
        )
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    with pytest.raises(DocDrQuotaExceeded) as exc_info:
        client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
    assert exc_info.value.status_code == 429
    assert exc_info.value.error_code == "quota_exceeded"
    assert "quota_exceeded" in exc_info.value.detail


@respx.mock
def test_503_malformed_output_raises_api_error():
    """503 response raises DocDrApiError with the detail message."""
    respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(
            503, json={"detail": "LLM returned malformed output; please retry."}
        )
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    with pytest.raises(DocDrApiError) as exc_info:
        client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
    assert exc_info.value.status_code == 503
    assert "malformed" in exc_info.value.detail.lower()


@respx.mock
def test_422_cost_protection_error_parsed():
    """422 with structured cost protection error is parsed correctly."""
    respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(
            422, json={"detail": {"error": "too_many_diffs", "limit": 20, "actual": 21}}
        )
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    with pytest.raises(DocDrApiError) as exc_info:
        client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
    assert exc_info.value.status_code == 422
    assert "too_many_diffs" in exc_info.value.error_code


# ── VAL-LLM-018: Action respects 429 by not retrying quota-exceeded ──────────


@respx.mock
def test_429_quota_exceeded_not_retried():
    """429 quota_exceeded is raised immediately without retry."""
    route = respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(
            429, json={"detail": {"error": "quota_exceeded", "used": 100, "quota": 100}}
        )
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    with pytest.raises(DocDrQuotaExceeded):
        client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
    # Only 1 request made, no retries
    assert route.call_count == 1


# ── VAL-LLM-019: Action retries on 503 with exponential backoff ──────────────


@respx.mock
def test_503_retried_then_succeeds():
    """503 is retried, and succeeds on the second attempt."""
    route = respx.post("http://localhost:8000/v1/maintenance").mock(
        side_effect=[
            httpx.Response(
                503, json={"detail": "LLM returned malformed output; please retry."}
            ),
            httpx.Response(
                200,
                json={
                    "updates": [{"path": "README.md", "content": "# OK"}],
                    "no_update": False,
                },
            ),
        ]
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    updates = client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
    assert len(updates) == 1
    assert updates[0].path == "README.md"
    assert route.call_count == 2


@respx.mock
def test_503_retried_then_finally_raises():
    """503 retried max times then raises DocDrApiError."""
    route = respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(
            503, json={"detail": "LLM service temporarily unavailable; please retry."}
        )
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    with pytest.raises(DocDrApiError) as exc_info:
        client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
    assert exc_info.value.status_code == 503
    # 3 attempts (original + 2 retries)
    assert route.call_count == 3


@respx.mock
def test_bootstrap_503_retried_then_succeeds():
    """Bootstrap 503 is retried, and succeeds on the third attempt."""
    route = respx.post("http://localhost:8000/v1/bootstrap").mock(
        side_effect=[
            httpx.Response(
                503, json={"detail": "LLM returned empty response; please retry."}
            ),
            httpx.Response(
                503,
                json={"detail": "LLM service temporarily unavailable; please retry."},
            ),
            httpx.Response(
                200,
                json={
                    "updates": [{"path": "README.md", "content": "# Gen"}],
                    "no_update": False,
                },
            ),
        ]
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    updates = client.send_bootstrap(repo="owner/repo", tree="src/", manifests=[])
    assert len(updates) == 1
    assert route.call_count == 3


@respx.mock
def test_422_not_retried():
    """422 is NOT retried — raised immediately."""
    route = respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(422, json={"detail": "Validation error"})
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    with pytest.raises(DocDrApiError) as exc_info:
        client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
    assert exc_info.value.status_code == 422
    assert route.call_count == 1


@respx.mock
def test_403_not_retried():
    """403 is NOT retried — raised immediately."""
    route = respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(403, json={"detail": "Invalid license key"})
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="bad-key")
    with pytest.raises(DocDrApiError):
        client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
    assert route.call_count == 1


# ── VAL-CROSS-025: LLM error does not create ghost PRs ──────────────────────
# When the backend returns 503 (LLM failure), create_or_update_pr and
# commit_and_push must NEVER be called. The action should exit cleanly
# without touching git or creating any PR.


def _make_completed_process(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _mock_action_config():
    """Return a mock ActionConfig with minimal required fields."""
    cfg = MagicMock()
    cfg.api_url = "http://localhost:8000"
    cfg.license_key = "test-key"
    cfg.github_repository = "owner/repo"
    cfg.github_sha = "abc1234567890"
    cfg.repo_path = "/tmp/fake-repo"
    return cfg


def test_action_passes_repo_config_paths_to_doc_scanners():
    mock_cfg = _mock_action_config()

    with (
        patch.object(action_mod, "ActionConfig", return_value=mock_cfg),
        patch.object(action_mod, "DocDrClient") as MockClient,
        patch.object(action_mod, "get_doc_files", return_value=[DocFile(path="README.md", content="# Hi")]) as mock_docs,
        patch.object(action_mod, "get_real_doc_files", return_value=[DocFile(path="README.md", content="# Hi")]) as mock_real_docs,
        patch.object(action_mod, "get_git_diff", return_value="diff --git a/src/main.py\n+def foo(): pass"),
        patch.object(action_mod, "parse_diff", return_value=[FileDiff(path="src/main.py", diff="+def foo(): pass")]),
        patch.object(action_mod, "filter_diff", return_value=[FileDiff(path="src/main.py", diff="+def foo(): pass")]),
        patch.object(action_mod, "scan_for_secrets", return_value=[]),
        patch.object(action_mod, "redact_secrets", side_effect=lambda x: x),
    ):
        client_instance = MockClient.return_value
        client_instance.get_repo_config.return_value = {
            "mode": "maintenance",
            "watched_paths": ["README.md"],
            "ignored_paths": ["CHANGELOG.md"],
        }
        client_instance.send_maintenance.return_value = []

        with pytest.raises(SystemExit) as exc_info:
            action_mod.main()

    assert exc_info.value.code == 0
    mock_docs.assert_called_once_with(
        mock_cfg.repo_path,
        include_paths=["README.md"],
        exclude_paths=["CHANGELOG.md"],
    )
    mock_real_docs.assert_called_once_with(
        mock_cfg.repo_path,
        include_paths=["README.md"],
        exclude_paths=["CHANGELOG.md"],
    )


def test_maintenance_503_does_not_create_ghost_pr():
    """VAL-CROSS-025: When backend returns 503, neither commit_and_push
    nor create_or_update_pr is called during maintenance mode."""
    mock_cfg = _mock_action_config()

    with (
        patch.object(action_mod, "ActionConfig", return_value=mock_cfg),
        patch.object(action_mod, "DocDrClient") as MockClient,
        patch.object(action_mod, "commit_and_push") as mock_commit,
        patch.object(action_mod, "create_or_update_pr") as mock_pr,
        patch.object(action_mod, "checkout_or_update_branch") as mock_checkout,
        patch.object(action_mod, "write_updates") as mock_write,
        patch.object(action_mod, "find_existing_doc_pr", return_value=None),
        patch.object(action_mod, "get_doc_files", return_value=[]),
        patch.object(action_mod, "get_real_doc_files", return_value=[DocFile(path="README.md", content="# Hi")]),
        patch.object(action_mod, "get_git_diff", return_value="diff --git a/src/main.py\n+def foo(): pass"),
        patch.object(action_mod, "parse_diff", return_value=[FileDiff(path="src/main.py", diff="+def foo(): pass")]),
        patch.object(action_mod, "filter_diff", return_value=[FileDiff(path="src/main.py", diff="+def foo(): pass")]),
        patch.object(action_mod, "scan_for_secrets", return_value=[]),
        patch.object(action_mod, "redact_secrets", side_effect=lambda x: x),
    ):
        # Client instance should raise on send_maintenance (503 after retries)
        client_instance = MockClient.return_value
        client_instance.get_repo_config.return_value = None
        client_instance.send_maintenance.side_effect = DocDrApiError(
            status_code=503, detail="LLM service temporarily unavailable; please retry."
        )
        client_instance.report_pr = MagicMock()

        with pytest.raises(SystemExit) as exc_info:
            action_mod.main()

    assert exc_info.value.code == 1
    mock_commit.assert_not_called()
    mock_pr.assert_not_called()
    mock_checkout.assert_not_called()
    mock_write.assert_not_called()


def test_bootstrap_503_does_not_create_ghost_pr():
    """VAL-CROSS-025: When backend returns 503, neither commit_and_push
    nor create_or_update_pr is called during bootstrap mode."""
    mock_cfg = _mock_action_config()

    # In bootstrap mode, action.py calls subprocess.run directly for `git ls-files`
    # and Path.exists() for manifest file checks — both must be mocked.
    empty_tree = _make_completed_process("")

    # Make Path(...) return a mock where (Path(...) / name).exists() → False
    # so no manifests are read.
    mock_path_instance = MagicMock()
    mock_path_instance.exists.return_value = False
    mock_path_instance.__truediv__ = MagicMock(return_value=mock_path_instance)

    # Patch the subprocess module at the action module level
    mock_sub = MagicMock()
    mock_sub.run.return_value = empty_tree

    with (
        patch.object(action_mod, "ActionConfig", return_value=mock_cfg),
        patch.object(action_mod, "DocDrClient") as MockClient,
        patch.object(action_mod, "commit_and_push") as mock_commit,
        patch.object(action_mod, "create_or_update_pr") as mock_pr,
        patch.object(action_mod, "checkout_or_update_branch") as mock_checkout,
        patch.object(action_mod, "write_updates") as mock_write,
        patch.object(action_mod, "find_existing_doc_pr", return_value=None),
        patch.object(action_mod, "get_doc_files", return_value=[]),
        patch.object(action_mod, "get_real_doc_files", return_value=[]),  # empty → bootstrap mode
        patch.object(action_mod, "get_entry_points", return_value=[]),
        patch.object(action_mod, "redact_secrets", side_effect=lambda x: x),
        patch.object(action_mod, "subprocess", mock_sub),
        patch.object(action_mod, "Path", return_value=mock_path_instance),
    ):
        # Client instance should raise on send_bootstrap (503 after retries)
        client_instance = MockClient.return_value
        client_instance.get_repo_config.return_value = None
        client_instance.send_bootstrap.side_effect = DocDrApiError(
            status_code=503, detail="LLM service temporarily unavailable; please retry."
        )
        client_instance.report_pr = MagicMock()

        with pytest.raises(SystemExit) as exc_info:
            action_mod.main()

    assert exc_info.value.code == 1
    mock_commit.assert_not_called()
    mock_pr.assert_not_called()
    mock_checkout.assert_not_called()
    mock_write.assert_not_called()
