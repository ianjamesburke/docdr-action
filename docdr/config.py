import os


def require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Required env var {name!r} is not set. Set it before running."
        )
    return val


class ActionConfig:
    def __init__(self):
        self.api_url: str = os.getenv("DOCDR_API_URL", "https://docdr.dev")
        self.github_token: str = require_env("GITHUB_TOKEN")
        self.github_repository: str = require_env("GITHUB_REPOSITORY")
        self.github_sha: str = os.getenv("GITHUB_SHA", "unknown")
        self.repo_path: str = os.getenv("GITHUB_WORKSPACE", ".")
