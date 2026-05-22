import re
from dataclasses import dataclass

EXCLUDED_PATTERNS = [
    r"-lock\.json$",
    r"\.lock$",
    r"\.min\.(js|css)$",
    r"\.svg$",
    r"(^|/)dist/",
    r"(^|/)build/",
    r"(^|/)\.next/",
    r"(^|/)node_modules/",
    r"(^|/)__pycache__/",
    r"\.egg-info/",
    r"\.pyc$",
    r"(^|/)coverage/",
    r"(^|/)\.coverage",
]


@dataclass
class FileDiff:
    path: str
    diff: str


def parse_diff(raw: str) -> list[FileDiff]:
    results = []
    current_path: str | None = None
    current_lines: list[str] = []

    for line in raw.splitlines():
        if line.startswith("diff --git "):
            if current_path is not None and current_lines:
                results.append(FileDiff(path=current_path, diff="\n".join(current_lines)))
            match = re.search(r" b/(.+)$", line)
            current_path = match.group(1) if match else None
            current_lines = [line]
        elif current_path is not None:
            current_lines.append(line)

    if current_path is not None and current_lines:
        results.append(FileDiff(path=current_path, diff="\n".join(current_lines)))

    return results


def filter_diff(diffs: list[FileDiff]) -> list[FileDiff]:
    return [
        d for d in diffs
        if not any(re.search(pat, d.path) for pat in EXCLUDED_PATTERNS)
    ]
