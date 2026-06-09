from dataclasses import dataclass
import json
import logging
import time

import httpx

from docdr.diff import FileDiff

logger = logging.getLogger(__name__)

# Retry configuration for transient failures (503)
_MAX_RETRIES = 3
_BASE_BACKOFF = 2.0  # seconds


class DocDrApiError(Exception):
    """Structured error from DocDr API with status code and parsed detail."""

    def __init__(
        self, status_code: int, detail: str, error_code: str = "", raw: str = ""
    ):
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code
        self.raw = raw
        super().__init__(f"[{status_code}] {detail}")


class DocDrQuotaExceeded(DocDrApiError):
    """Raised when the server returns 429 with quota_exceeded — should NOT be retried."""

    pass


def _parse_error_response(resp: httpx.Response) -> DocDrApiError:
    """Parse a non-2xx response into a structured error."""
    try:
        data = resp.json()
        detail = data.get("detail", "")
        # detail can be a string or a dict with error/limit/actual fields
        if isinstance(detail, dict):
            error_code = detail.get("error", "")
            detail_str = json.dumps(detail)
        else:
            error_code = ""
            detail_str = str(detail)
    except (json.JSONDecodeError, ValueError):
        detail_str = resp.text[:500]
        error_code = ""

    if resp.status_code == 429 and error_code == "quota_exceeded":
        return DocDrQuotaExceeded(
            status_code=resp.status_code,
            detail=detail_str,
            error_code=error_code,
            raw=resp.text[:500],
        )
    return DocDrApiError(
        status_code=resp.status_code,
        detail=detail_str,
        error_code=error_code,
        raw=resp.text[:500],
    )


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
        return self._request_with_retry(
            "POST",
            "/v1/maintenance",
            json={
                "license_key": self.license_key,
                "repo": repo,
                "diffs": [{"path": d.path, "diff": d.diff} for d in diffs],
                "doc_files": [
                    {"path": f.path, "content": f.content} for f in doc_files
                ],
            },
        )

    def send_bootstrap(
        self,
        repo: str,
        tree: str,
        manifests: list[DocFile],
        source_files: list[DocFile] | None = None,
    ) -> list[FileUpdate]:
        return self._request_with_retry(
            "POST",
            "/v1/bootstrap",
            json={
                "license_key": self.license_key,
                "repo": repo,
                "tree": tree,
                "manifests": [
                    {"path": m.path, "content": m.content} for m in manifests
                ],
                "source_files": [
                    {"path": f.path, "content": f.content} for f in (source_files or [])
                ],
            },
        )

    def _request_with_retry(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> list[FileUpdate]:
        """Make an HTTP request with retry logic for transient LLM failures (503).

        Retry behavior:
        - 503 (LLM transient failure): retry up to _MAX_RETRIES times with exponential backoff
        - 429 (quota exceeded): never retry, raise immediately
        - 403, 422, other: never retry, raise immediately
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                with httpx.Client(timeout=120) as client:
                    resp = client.request(
                        method,
                        f"{self.base_url}{path}",
                        **kwargs,
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    return [FileUpdate(**u) for u in data["updates"]]

                err = _parse_error_response(resp)
                # 429 quota_exceeded: never retry
                if isinstance(err, DocDrQuotaExceeded):
                    raise err
                # 503 (LLM transient): retry with backoff
                if resp.status_code == 503 and attempt < _MAX_RETRIES - 1:
                    backoff = _BASE_BACKOFF * (2**attempt)
                    logger.warning(
                        "[DocDr] Got 503 (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        _MAX_RETRIES,
                        backoff,
                        err.detail,
                    )
                    time.sleep(backoff)
                    last_exc = err
                    continue
                # All other errors: raise immediately
                raise err
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < _MAX_RETRIES - 1:
                    backoff = _BASE_BACKOFF * (2**attempt)
                    logger.warning(
                        "[DocDr] Network error (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        _MAX_RETRIES,
                        backoff,
                        exc,
                    )
                    time.sleep(backoff)
                    last_exc = exc
                    continue
                raise DocDrApiError(
                    status_code=0,
                    detail=f"Network error after {_MAX_RETRIES} attempts: {exc}",
                ) from exc

        # Exhausted retries
        if last_exc is not None:
            if isinstance(last_exc, DocDrApiError):
                raise last_exc
            raise DocDrApiError(
                status_code=0,
                detail=f"Failed after {_MAX_RETRIES} retries: {last_exc}",
            ) from last_exc
        raise DocDrApiError(status_code=0, detail="Unknown error")

    def get_repo_config(self, repo: str) -> dict | None:
        """Fetch server-side doc mode config. Returns dict or None on any failure."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    f"{self.base_url}/v1/repo-config",
                    json={"license_key": self.license_key, "repo": repo},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return None

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
