import json
import subprocess
import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from docdr.client import DocFile, FileUpdate

_METADATA_FILES = {
    "CHANGELOG.md", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md",
    "LICENSE.md", "AUTHORS.md", "SECURITY.md",
}

_ENTRY_POINT_HEURISTICS = [
    "main.py", "app.py", "index.ts", "index.js", "lib.rs",
    "src/main.py", "src/app.py", "src/main.ts", "src/index.ts",
    "src/index.js", "src/lib.rs", "src/main.rs",
]

_ENTRY_POINT_BUDGET = 50_000  # bytes
_DOC_FILE_EXTENSIONS = (".md", ".mdx", ".rst", ".txt")


@dataclass(frozen=True)
class DocDrIgnorePattern:
    pattern: str
    negated: bool = False
    anchored: bool = False


def parse_docdrignore(content: str) -> list[DocDrIgnorePattern]:
    patterns: list[DocDrIgnorePattern] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        if not line:
            continue
        anchored = line.startswith("/")
        line = line.lstrip("/")
        if line.endswith("/"):
            line = line.rstrip("/") + "/**"
        patterns.append(DocDrIgnorePattern(pattern=line, negated=negated, anchored=anchored))
    return patterns


def load_docdrignore(repo_path: str = ".") -> list[DocDrIgnorePattern]:
    ignore_path = Path(repo_path) / ".docdrignore"
    if not ignore_path.exists() or not ignore_path.is_file():
        return []
    return parse_docdrignore(ignore_path.read_text(encoding="utf-8", errors="replace"))


def is_docdrignored(path: str, patterns: list[DocDrIgnorePattern]) -> bool:
    normalized = PurePosixPath(path).as_posix().lstrip("./")
    ignored = False
    for item in patterns:
        if _pattern_matches(normalized, item.pattern, item.anchored):
            ignored = not item.negated
    return ignored


def _pattern_matches(path: str, pattern: str, anchored: bool) -> bool:
    if not pattern:
        return False
    if anchored:
        return fnmatch.fnmatchcase(path, pattern)
    if "/" not in pattern:
        return fnmatch.fnmatchcase(PurePosixPath(path).name, pattern) or fnmatch.fnmatchcase(path, f"**/{pattern}")
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path, f"**/{pattern}")


def _is_doc_like_path(path: str) -> bool:
    return path.lower().endswith(_DOC_FILE_EXTENSIONS) or path.startswith("docs/")



def _validate_update_path(path: str) -> None:
    """Raise ValueError if path is unsafe to write."""
    if not path:
        raise ValueError("Empty path")

    p = Path(path)

    if p.is_absolute():
        raise ValueError(f"Absolute path rejected: {path!r}")

    # Normalize and check for traversal
    try:
        p.resolve(strict=False)
    except Exception:
        raise ValueError(f"Unresolvable path: {path!r}")

    # Reject .. traversal by checking normalized form against a known base
    # posixpath approach: reject if any part is ".."
    for part in p.parts:
        if part == "..":
            raise ValueError(f"Path traversal rejected: {path!r}")

    # Reject hidden files/directories
    for part in p.parts:
        if part.startswith("."):
            raise ValueError(f"Hidden path rejected: {path!r}")

    # Must be a .md file or under docs/
    is_md = path.endswith(".md")
    is_under_docs = path.startswith("docs/")
    if not (is_md or is_under_docs):
        raise ValueError(f"Path not allowed (must be *.md or under docs/): {path!r}")


def find_existing_doc_pr(repo_path: str = ".") -> str | None:
    """Return the branch name of an existing open Draft PR with docdr- prefix, or None."""
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--state", "open",
                "--draft",
                "--json", "headRefName,number",
            ],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=30,
        )
        if result.returncode != 0:
            return None

        prs = json.loads(result.stdout)
        for pr in prs:
            if pr.get("headRefName", "").startswith("docdr-"):
                return pr["headRefName"]
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        # gh not available, timeout, or bad output: fall back to new branch
        return None
    return None


def get_real_doc_files(
    repo_path: str = ".",
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
) -> list[DocFile]:
    """Return only 'real' documentation files: README.md and files under docs/.
    Excludes metadata-only files like CHANGELOG.md, CONTRIBUTING.md, etc.
    Used by detect_mode to decide bootstrap vs maintenance."""
    result = subprocess.run(
        ["git", "-C", repo_path, "ls-files"],
        capture_output=True,
        text=True,
    )
    files = []
    include_set = set(include_paths or [])
    exclude_set = set(exclude_paths or [])
    ignore_patterns = load_docdrignore(repo_path)
    for rel_path in result.stdout.splitlines():
        if include_set and rel_path not in include_set:
            continue
        if rel_path in exclude_set or is_docdrignored(rel_path, ignore_patterns):
            continue
        # Only README.md at root or anything under docs/
        is_readme = rel_path == "README.md"
        is_under_docs = rel_path.startswith("docs/")
        # Exclude known metadata-only files
        filename = Path(rel_path).name
        if (is_readme or is_under_docs) and filename not in _METADATA_FILES:
            full = Path(repo_path) / rel_path
            if full.exists() and full.is_file():
                try:
                    files.append(DocFile(path=rel_path, content=full.read_text(encoding="utf-8", errors="replace")))
                except Exception:
                    continue
    return files


def get_entry_points(repo_path: str, manifests: list[DocFile]) -> list[DocFile]:
    """Detect and read entry point files up to a 50KB total budget.

    1. Parse manifests for declared entry points.
    2. Fall back to heuristic paths.
    Returns a list of DocFile with content read from disk."""
    candidates: list[str] = []

    for m in manifests:
        if m.path == "package.json":
            try:
                data = json.loads(m.content)
                main = data.get("main")
                if main and isinstance(main, str):
                    candidates.append(main)
            except (json.JSONDecodeError, ValueError):
                pass
        elif m.path in ("pyproject.toml", "setup.cfg"):
            # Scan for [project.scripts] or [tool.poetry.scripts]
            in_scripts = False
            for line in m.content.splitlines():
                stripped = line.strip()
                if stripped.startswith("[") and "scripts" in stripped:
                    in_scripts = True
                    continue
                if stripped.startswith("[") and in_scripts:
                    break
                if in_scripts and "=" in stripped:
                    # value like: myapp = "myapp.main:main" → myapp/main.py
                    val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    if ":" in val:
                        module_path = val.split(":")[0].replace(".", "/") + ".py"
                        candidates.append(module_path)
        elif m.path == "Cargo.toml":
            in_bin = False
            for line in m.content.splitlines():
                stripped = line.strip()
                if stripped == "[[bin]]":
                    in_bin = True
                    continue
                if in_bin and stripped.startswith("path"):
                    val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    candidates.append(val)
                    in_bin = False

    # Add heuristics (only those not already found)
    for h in _ENTRY_POINT_HEURISTICS:
        if h not in candidates:
            candidates.append(h)

    results: list[DocFile] = []
    budget = _ENTRY_POINT_BUDGET
    seen: set[str] = set()
    for rel_path in candidates:
        if rel_path in seen:
            continue
        if ".." in Path(rel_path).parts:
            continue
        seen.add(rel_path)
        full = (Path(repo_path) / rel_path).resolve()
        if not full.resolve().is_relative_to(Path(repo_path).resolve()):
            continue
        if not full.exists() or not full.is_file():
            continue
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(content) > budget:
            content = content[:budget]
        budget -= len(content)
        results.append(DocFile(path=rel_path, content=content))
        if budget <= 0:
            break
    return results


def get_doc_files(
    repo_path: str = ".",
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
) -> list[DocFile]:
    """Find all markdown files in the repo (*.md files and anything under docs/)."""
    result = subprocess.run(
        ["git", "-C", repo_path, "ls-files"],
        capture_output=True,
        text=True,
    )
    files = []
    include_set = set(include_paths or [])
    exclude_set = set(exclude_paths or [])
    ignore_patterns = load_docdrignore(repo_path)
    for rel_path in result.stdout.splitlines():
        if include_set and rel_path not in include_set:
            continue
        if rel_path in exclude_set or is_docdrignored(rel_path, ignore_patterns):
            continue
        # Include doc-like files and everything under docs/
        if _is_doc_like_path(rel_path):
            full = Path(repo_path) / rel_path
            if full.exists() and full.is_file():
                files.append(DocFile(path=rel_path, content=full.read_text(encoding="utf-8", errors="replace")))
    return files


def checkout_or_update_branch(branch_name: str, repo_path: str = ".") -> None:
    """Create a new branch or switch to existing one (fetching from remote if needed)."""
    local = subprocess.run(
        ["git", "-C", repo_path, "branch", "--list", branch_name],
        capture_output=True, text=True,
    )
    if branch_name in local.stdout:
        subprocess.run(["git", "-C", repo_path, "checkout", branch_name], check=True, capture_output=True)
        return

    # Check if branch exists on remote
    remote = subprocess.run(
        ["git", "-C", repo_path, "ls-remote", "--heads", "origin", branch_name],
        capture_output=True, text=True,
    )
    if branch_name in remote.stdout:
        subprocess.run(
            ["git", "-C", repo_path, "fetch", "origin", branch_name],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", repo_path, "checkout", "-b", branch_name, f"origin/{branch_name}"],
            check=True, capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "-C", repo_path, "checkout", "-b", branch_name],
            check=True, capture_output=True,
        )


def write_updates(updates: list[FileUpdate], repo_path: str = ".") -> None:
    """Write file updates to disk and stage them in git."""
    repo = Path(repo_path).resolve()
    for update in updates:
        _validate_update_path(update.path)
        dest = (repo / update.path).resolve()
        # Reject symlink escapes: resolved path must stay inside the repo
        try:
            dest.relative_to(repo)
        except ValueError:
            raise ValueError(f"Path escapes repo root: {update.path!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(update.content)
        subprocess.run(["git", "-C", repo_path, "add", update.path], check=True)


def commit_and_push(branch_name: str, message: str, repo_path: str = ".") -> None:
    """Commit changes and push to origin."""
    subprocess.run(
        ["git", "-C", repo_path, "config", "user.email", "docdr[bot]@users.noreply.github.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", repo_path, "config", "user.name", "docdr[bot]"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", repo_path, "commit", "-m", message],
        check=True,
    )
    subprocess.run(
        ["git", "-C", repo_path, "push", "-u", "origin", branch_name],
        check=True,
    )


def create_or_update_pr(branch_name: str, title: str, body: str, repo_path: str = ".") -> str:
    """Creates draft PR; if one already exists, returns empty string (push to branch is enough)."""
    result = subprocess.run(
        ["gh", "pr", "create", "--draft", "--title", title, "--body", body],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "already exists" in stderr or "already a pull request" in stderr:
            return ""
        print(f"[DocDr] gh pr create failed: {stderr}", flush=True)
        return ""
    lines = result.stdout.strip().split("\n")
    return lines[-1]  # URL is last line
