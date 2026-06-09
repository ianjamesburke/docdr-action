import os
import tempfile
from dataclasses import dataclass

from detect_secrets import SecretsCollection
from detect_secrets.settings import default_settings


@dataclass
class SecretMatch:
    pattern_name: str
    line_number: int


def _scan_content(content: str) -> list[SecretMatch]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tmp", delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp = f.name
    try:
        col = SecretsCollection()
        with default_settings():
            col.scan_file(tmp)
        matches: list[SecretMatch] = []
        for _, secret in col:
            matches.append(SecretMatch(pattern_name=secret.type, line_number=secret.line_number))
        return matches
    finally:
        os.unlink(tmp)


def scan_for_secrets(content: str) -> list[SecretMatch]:
    return _scan_content(content)


def redact_secrets(content: str) -> str:
    """Replace any line containing a secret with [REDACTED].

    Use for outbound doc_files/manifests/tree where blocking entirely would be
    too aggressive. Diffs use scan_for_secrets and abort instead.
    """
    matches = _scan_content(content)
    if not matches:
        return content

    secret_lines = {m.line_number for m in matches}
    lines = content.splitlines(keepends=True)
    result = []
    for i, line in enumerate(lines, start=1):
        if i in secret_lines:
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
            result.append(f"[REDACTED]{ending}")
        else:
            result.append(line)
    return "".join(result)
