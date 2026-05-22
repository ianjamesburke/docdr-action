import subprocess
from pathlib import Path
import pytest
from docdr.git import get_doc_files, write_updates, checkout_or_update_branch, _validate_update_path
from docdr.client import FileUpdate


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo with a README."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    readme = tmp_path / "README.md"
    readme.write_text("# Hello")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)
    return tmp_path


def test_get_doc_files_finds_readme(git_repo):
    docs = get_doc_files(str(git_repo))
    paths = [d.path for d in docs]
    assert "README.md" in paths


def test_get_doc_files_returns_content(git_repo):
    docs = get_doc_files(str(git_repo))
    readme = next(d for d in docs if d.path == "README.md")
    assert readme.content == "# Hello"


def test_checkout_creates_new_branch(git_repo):
    checkout_or_update_branch("ai-docs-test", str(git_repo))
    result = subprocess.run(
        ["git", "-C", str(git_repo), "branch", "--show-current"],
        capture_output=True, text=True
    )
    assert result.stdout.strip() == "ai-docs-test"


def test_write_updates_creates_file(git_repo):
    checkout_or_update_branch("ai-docs-test", str(git_repo))
    updates = [FileUpdate(path="docs/api.md", content="# API Docs")]
    write_updates(updates, str(git_repo))
    assert (git_repo / "docs" / "api.md").read_text() == "# API Docs"


# --- _validate_update_path acceptance cases ---

@pytest.mark.parametrize("path", [
    "README.md",
    "docs/api.md",
    "docs/guide/setup.md",
    "CHANGELOG.md",
    "docs/images/diagram.png",  # under docs/ but not .md — allowed
])
def test_validate_path_accepts_valid(path):
    _validate_update_path(path)  # must not raise


# --- _validate_update_path rejection cases ---

@pytest.mark.parametrize("path", [
    "/etc/passwd",
    "/absolute/path.md",
])
def test_validate_path_rejects_absolute(path):
    with pytest.raises(ValueError, match="Absolute path"):
        _validate_update_path(path)


@pytest.mark.parametrize("path", [
    "../etc/passwd",
    "docs/../../../etc/shadow",
    "a/b/../../.env",
])
def test_validate_path_rejects_traversal(path):
    with pytest.raises(ValueError, match="traversal"):
        _validate_update_path(path)


@pytest.mark.parametrize("path", [
    ".github/workflows/evil.md",
    ".git/config",
    ".env",
    ".hidden/file.md",
])
def test_validate_path_rejects_hidden(path):
    with pytest.raises(ValueError, match="Hidden path"):
        _validate_update_path(path)


@pytest.mark.parametrize("path", [
    "src/main.py",
    "package.json",
    "Makefile",
    "script.sh",
])
def test_validate_path_rejects_non_doc(path):
    with pytest.raises(ValueError, match="not allowed"):
        _validate_update_path(path)


def test_validate_path_rejects_empty():
    with pytest.raises(ValueError, match="Empty path"):
        _validate_update_path("")


def test_write_updates_rejects_traversal(git_repo):
    checkout_or_update_branch("ai-docs-test", str(git_repo))
    updates = [FileUpdate(path="../evil.md", content="bad")]
    with pytest.raises(ValueError):
        write_updates(updates, str(git_repo))


def test_write_updates_rejects_absolute(git_repo):
    checkout_or_update_branch("ai-docs-test", str(git_repo))
    updates = [FileUpdate(path="/tmp/evil.md", content="bad")]
    with pytest.raises(ValueError):
        write_updates(updates, str(git_repo))


def test_write_updates_rejects_hidden(git_repo):
    checkout_or_update_branch("ai-docs-test", str(git_repo))
    updates = [FileUpdate(path=".github/workflows/evil.md", content="bad")]
    with pytest.raises(ValueError):
        write_updates(updates, str(git_repo))
