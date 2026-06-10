import subprocess
import sys
from pathlib import Path

from docdr.client import DocDrClient, DocDrApiError, DocDrQuotaExceeded, DocFile
from docdr.config import ActionConfig
from docdr.diff import filter_diff, parse_diff
from docdr.git import (
    checkout_or_update_branch,
    commit_and_push,
    create_or_update_pr,
    find_existing_doc_pr,
    get_doc_files,
    get_entry_points,
    get_real_doc_files,
    write_updates,
)
from docdr.scanner import redact_secrets, scan_for_secrets


def get_git_diff(repo_path: str, sha: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, "diff", f"{sha}^..{sha}"],
        capture_output=True,
        text=True,
    )
    return result.stdout


def detect_mode(real_doc_files: list) -> str:
    """Bootstrap if no real docs (README.md or docs/*) exist; otherwise maintenance."""
    return "bootstrap" if not real_doc_files else "maintenance"


def main():
    try:
        cfg = ActionConfig()
    except RuntimeError as e:
        print(f"[DocDr] Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    client = DocDrClient(
        base_url=cfg.api_url,
        github_token=cfg.github_token,
    )

    # Check server-side config for explicit mode override
    server_config = client.get_repo_config(cfg.github_repository)
    server_mode = server_config.get("mode") if server_config else None
    watched_paths = server_config.get("watched_paths", []) if server_config else []
    ignored_paths = server_config.get("ignored_paths", []) if server_config else []

    if server_mode == "skip":
        print("[DocDr] Mode: skip (configured via DocDr dashboard). Exiting.")
        sys.exit(0)

    # Get current doc files
    print("[DocDr] Scanning repository...")
    doc_files = get_doc_files(
        cfg.repo_path, include_paths=watched_paths, exclude_paths=ignored_paths
    )
    real_doc_files = get_real_doc_files(
        cfg.repo_path, include_paths=watched_paths, exclude_paths=ignored_paths
    )

    if server_mode and server_mode != "auto":
        mode = server_mode
        print(
            f"[DocDr] Mode: {mode} (configured via DocDr dashboard, {len(real_doc_files)} doc files found)"
        )
    else:
        mode = detect_mode(real_doc_files)
        print(f"[DocDr] Documentation files found: {len(real_doc_files)}")
        print(f"[DocDr] Mode: {mode}")

    if mode == "maintenance":
        raw_diff = get_git_diff(cfg.repo_path, cfg.github_sha)
        if not raw_diff.strip():
            print("[DocDr] No diff found, exiting.")
            sys.exit(0)

        diffs = filter_diff(parse_diff(raw_diff))
        if not diffs:
            print(
                "[DocDr] All changed files are noise (lockfiles, bundles, etc.), exiting."
            )
            sys.exit(0)

        # Secret scan diffs — abort if secrets found (diffs should never contain real secrets)
        for d in diffs:
            secrets = scan_for_secrets(d.diff)
            if secrets:
                print(
                    f"[DocDr] Secret detected in {d.path}: {secrets[0].pattern_name} at line {secrets[0].line_number}"
                )
                print("[DocDr] Aborting to protect your credentials.")
                sys.exit(1)

        # Redact doc files before sending (may contain env var examples; redact rather than abort)
        sanitized_doc_files = [
            DocFile(path=f.path, content=redact_secrets(f.content)) for f in doc_files
        ]

        print(f"[DocDr] Sending {len(diffs)} file diffs to DocDr API...")
        try:
            updates = client.send_maintenance(
                repo=cfg.github_repository,
                diffs=diffs,
                doc_files=sanitized_doc_files,
            )
        except DocDrQuotaExceeded as exc:
            print(f"[DocDr] Error: Quota exceeded — {exc.detail}", file=sys.stderr)
            sys.exit(1)
        except DocDrApiError as exc:
            print(f"[DocDr] Error [{exc.status_code}]: {exc.detail}", file=sys.stderr)
            sys.exit(1)
    else:
        # Bootstrap: scan repo structure
        tree_result = subprocess.run(
            ["git", "-C", cfg.repo_path, "ls-files"],
            capture_output=True,
            text=True,
        )
        tree = redact_secrets(tree_result.stdout)

        # Read manifest files and redact before sending
        manifest_names = [
            "pyproject.toml",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "requirements.txt",
            "Gemfile",
            "pom.xml",
            "build.gradle",
            "composer.json",
            "mix.exs",
            "Makefile",
            "CMakeLists.txt",
            "setup.py",
            "setup.cfg",
        ]
        manifests = []
        for name in manifest_names:
            p = Path(cfg.repo_path) / name
            if p.exists():
                raw_content = p.read_text(encoding="utf-8", errors="replace")[:2000]
                manifests.append(
                    DocFile(path=name, content=redact_secrets(raw_content))
                )

        source_files = get_entry_points(cfg.repo_path, manifests)

        manifest_names_found = [m.path for m in manifests]
        entry_paths = [s.path for s in source_files]
        source_kb = sum(len(s.content) for s in source_files) // 1024
        print(
            f"[DocDr] Entry points detected: {', '.join(entry_paths) if entry_paths else 'none'}"
        )
        print(
            f"[DocDr] Manifest files: {', '.join(manifest_names_found) if manifest_names_found else 'none'} ({len(manifests)} found)"
        )
        print(f"[DocDr] Source context: {len(source_files)} files, {source_kb}KB total")
        print("[DocDr] Bootstrap mode: generating initial documentation...")
        try:
            updates = client.send_bootstrap(
                repo=cfg.github_repository,
                tree=tree,
                manifests=manifests,
                source_files=source_files,
            )
        except DocDrQuotaExceeded as exc:
            print(f"[DocDr] Error: Quota exceeded — {exc.detail}", file=sys.stderr)
            sys.exit(1)
        except DocDrApiError as exc:
            print(f"[DocDr] Error [{exc.status_code}]: {exc.detail}", file=sys.stderr)
            sys.exit(1)

    if not updates:
        print("[DocDr] No documentation updates needed.")
        sys.exit(0)

    print(f"[DocDr] Received {len(updates)} file update(s). Creating branch and PR...")

    existing_branch = find_existing_doc_pr(cfg.repo_path)
    if existing_branch:
        branch_name = existing_branch
        print(f"[DocDr] Found existing Draft PR on branch: {branch_name}")
    else:
        branch_name = f"docdr-{cfg.github_sha[:8]}"

    checkout_or_update_branch(branch_name, cfg.repo_path)
    write_updates(updates, cfg.repo_path)
    commit_and_push(branch_name, "docs: update documentation [DocDr]", cfg.repo_path)

    if existing_branch:
        # PR already exists, push was enough
        print(f"[DocDr] Pushed update to existing Draft PR branch: {branch_name}")
        pr_url = ""
        pr_status = "updated"
    else:
        pr_title = "DocDr: Update documentation"
        pr_body = (
            "Automated documentation update generated by [DocDr](https://docdr.dev).\n\n"
            "Review the changes and merge when ready. This PR was opened in Draft mode — "
            "it will never auto-merge.\n\n"
            "> This is an AI-generated update. Always review before merging."
        )
        pr_url = create_or_update_pr(branch_name, pr_title, pr_body, cfg.repo_path)
        if pr_url:
            print(f"[DocDr] Draft PR opened: {pr_url}")
            pr_status = "proposed"
        else:
            print(f"[DocDr] Pushed to existing branch: {branch_name}")
            pr_status = "updated"

    try:
        client.report_pr(
            repo=cfg.github_repository,
            mode=mode,
            status=pr_status,
            branch=branch_name,
            pr_url=pr_url,
        )
    except Exception as e:
        print(f"[DocDr] Warning: failed to report PR event: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
