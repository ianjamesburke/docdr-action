import subprocess
from unittest.mock import patch
import pytest
from docdr.git import (
    _validate_update_path,
    checkout_or_update_branch,
    find_existing_doc_pr,
    get_doc_files,
    get_entry_points,
    get_real_doc_files,
    is_docdrignored,
    parse_docdrignore,
    write_updates,
)
from docdr.client import DocFile, FileUpdate


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


# --- get_real_doc_files ---

@pytest.fixture
def repo_with_mixed_md(tmp_path):
    """Repo with README, docs/, and metadata files."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    files = {
        "README.md": "# Hello",
        "CHANGELOG.md": "## v1.0",
        "CODE_OF_CONDUCT.md": "Be nice.",
        "CONTRIBUTING.md": "How to contribute.",
        "LICENSE.md": "MIT",
        "docs/guide.md": "# Guide",
    }
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        subprocess.run(["git", "-C", str(tmp_path), "add", rel], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)
    return tmp_path


def test_get_real_doc_files_includes_readme(repo_with_mixed_md):
    docs = get_real_doc_files(str(repo_with_mixed_md))
    paths = [d.path for d in docs]
    assert "README.md" in paths


def test_get_real_doc_files_includes_docs_dir(repo_with_mixed_md):
    docs = get_real_doc_files(str(repo_with_mixed_md))
    paths = [d.path for d in docs]
    assert "docs/guide.md" in paths


def test_get_real_doc_files_excludes_metadata(repo_with_mixed_md):
    docs = get_real_doc_files(str(repo_with_mixed_md))
    paths = [d.path for d in docs]
    assert "CHANGELOG.md" not in paths
    assert "CODE_OF_CONDUCT.md" not in paths
    assert "CONTRIBUTING.md" not in paths
    assert "LICENSE.md" not in paths


def test_get_doc_files_still_returns_all_md(repo_with_mixed_md):
    """get_doc_files (maintenance flow) must remain unaffected."""
    docs = get_doc_files(str(repo_with_mixed_md))
    paths = [d.path for d in docs]
    assert "CHANGELOG.md" in paths
    assert "README.md" in paths
    assert "docs/guide.md" in paths


def test_docdrignore_parser_supports_negation():
    patterns = parse_docdrignore("docs/archive/**\n!docs/archive/keep.md\nCHANGELOG.md\n")
    assert is_docdrignored("docs/archive/old.md", patterns) is True
    assert is_docdrignored("docs/archive/keep.md", patterns) is False
    assert is_docdrignored("CHANGELOG.md", patterns) is True


def test_get_doc_files_excludes_docdrignored_files(repo_with_mixed_md):
    (repo_with_mixed_md / ".docdrignore").write_text("CHANGELOG.md\ndocs/guide.md\n")
    docs = get_doc_files(str(repo_with_mixed_md))
    paths = [d.path for d in docs]
    assert "README.md" in paths
    assert "CHANGELOG.md" not in paths
    assert "docs/guide.md" not in paths


def test_get_doc_files_respects_configured_paths(repo_with_mixed_md):
    docs = get_doc_files(
        str(repo_with_mixed_md),
        include_paths=["README.md", "docs/guide.md"],
        exclude_paths=["docs/guide.md"],
    )
    assert [d.path for d in docs] == ["README.md"]


# --- get_entry_points ---

@pytest.fixture
def repo_with_pyproject(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], check=True)
    (tmp_path / "pyproject.toml").write_text(
        "[project.scripts]\nmyapp = \"myapp.main:main\"\n"
    )
    (tmp_path / "myapp").mkdir()
    (tmp_path / "myapp" / "main.py").write_text("def main(): pass")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)
    return tmp_path


@pytest.fixture
def repo_with_package_json(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], check=True)
    (tmp_path / "package.json").write_text('{"name": "myapp", "main": "index.js"}')
    (tmp_path / "index.js").write_text("console.log('hello')")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)
    return tmp_path


@pytest.fixture
def repo_with_main_py_heuristic(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], check=True)
    (tmp_path / "main.py").write_text("if __name__ == '__main__': pass")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)
    return tmp_path


def test_get_entry_points_from_package_json(repo_with_package_json):
    manifests = [DocFile(path="package.json", content='{"name": "myapp", "main": "index.js"}')]
    entries = get_entry_points(str(repo_with_package_json), manifests)
    paths = [e.path for e in entries]
    assert "index.js" in paths


def test_get_entry_points_heuristic_main_py(repo_with_main_py_heuristic):
    entries = get_entry_points(str(repo_with_main_py_heuristic), [])
    paths = [e.path for e in entries]
    assert "main.py" in paths


def test_get_entry_points_returns_doc_files(repo_with_main_py_heuristic):
    entries = get_entry_points(str(repo_with_main_py_heuristic), [])
    assert all(hasattr(e, "path") and hasattr(e, "content") for e in entries)


def test_get_entry_points_budget_50kb(tmp_path):
    """Files totalling over 50KB should be truncated/excluded."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], check=True)
    big = "x" * 60_000
    (tmp_path / "main.py").write_text(big)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)
    entries = get_entry_points(str(tmp_path), [])
    total = sum(len(e.content) for e in entries)
    assert total <= 50_000


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


# --- find_existing_doc_pr tests ---


def _make_completed_process(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_find_existing_doc_pr_returns_branch_when_found():
    json_output = '[{"headRefName": "docdr-update-abc12345", "number": 42}]'
    with patch("docdr.git.subprocess.run", return_value=_make_completed_process(json_output)):
        assert find_existing_doc_pr("/fake") == "docdr-update-abc12345"


def test_find_existing_doc_pr_returns_none_when_no_match():
    json_output = '[{"headRefName": "feature-branch", "number": 10}]'
    with patch("docdr.git.subprocess.run", return_value=_make_completed_process(json_output)):
        assert find_existing_doc_pr("/fake") is None


def test_find_existing_doc_pr_returns_none_on_empty_list():
    with patch("docdr.git.subprocess.run", return_value=_make_completed_process("[]")):
        assert find_existing_doc_pr("/fake") is None


def test_find_existing_doc_pr_returns_none_when_gh_missing():
    with patch("docdr.git.subprocess.run", side_effect=FileNotFoundError):
        assert find_existing_doc_pr("/fake") is None


def test_find_existing_doc_pr_returns_none_on_nonzero_exit():
    with patch("docdr.git.subprocess.run", return_value=_make_completed_process("", returncode=1)):
        assert find_existing_doc_pr("/fake") is None
