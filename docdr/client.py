from dataclasses import dataclass

import httpx

from docdr.diff import FileDiff


@dataclass
class FileUpdate:
    path: str
    content: str


@dataclass
class DocFile:
    path: str
    content: str


class DocDrClient:
    def __init__(self, base_url: str, license_key: str):
        self.base_url = base_url.rstrip("/")
        self.license_key = license_key

    def send_maintenance(
        self,
        repo: str,
        diffs: list[FileDiff],
        doc_files: list[DocFile],
    ) -> list[FileUpdate]:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self.base_url}/v1/maintenance",
                json={
                    "license_key": self.license_key,
                    "repo": repo,
                    "diffs": [{"path": d.path, "diff": d.diff} for d in diffs],
                    "doc_files": [{"path": f.path, "content": f.content} for f in doc_files],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [FileUpdate(**u) for u in data["updates"]]

    def send_bootstrap(
        self,
        repo: str,
        tree: str,
        manifests: list[DocFile],
    ) -> list[FileUpdate]:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self.base_url}/v1/bootstrap",
                json={
                    "license_key": self.license_key,
                    "repo": repo,
                    "tree": tree,
                    "manifests": [{"path": m.path, "content": m.content} for m in manifests],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [FileUpdate(**u) for u in data["updates"]]

    def report_pr(
        self,
        repo: str,
        mode: str,
        status: str,
        branch: str | None = None,
        pr_url: str | None = None,
    ) -> None:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"{self.base_url}/v1/report",
                json={
                    "license_key": self.license_key,
                    "repo": repo,
                    "mode": mode,
                    "status": status,
                    "branch": branch,
                    "pr_url": pr_url,
                },
            )
            resp.raise_for_status()
