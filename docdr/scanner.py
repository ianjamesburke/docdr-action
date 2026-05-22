import re
from dataclasses import dataclass

SECRET_PATTERNS = [
    # Version control / CI
    ("GitHub PAT", r"ghp_[A-Za-z0-9]{36}"),
    ("GitHub OAuth", r"gho_[A-Za-z0-9]{36}"),
    # AWS
    ("AWS Access Key", r"AKIA[0-9A-Z]{16}"),
    ("AWS Secret Key", r"(?i)aws[_\-\s]?secret[_\-\s]?(?:access[_\-\s]?)?key\s*[=:]\s*['\"]?[A-Za-z0-9/+]{40}['\"]?"),
    # GCP
    ("GCP Service Account", r'"type"\s*:\s*"service_account"'),
    # Stripe
    ("Stripe Live Key", r"sk_live_[A-Za-z0-9]{24,}"),
    ("Stripe Restricted Key", r"rk_live_[A-Za-z0-9]{24,}"),
    # OpenAI / OpenRouter
    ("OpenAI Key", r"sk-[A-Za-z0-9]{48}"),
    ("OpenRouter Key", r"sk-or-[A-Za-z0-9\-_]{20,}"),
    # Slack
    ("Slack Bot Token", r"xoxb-[0-9]{10,}-[0-9]{10,}-[A-Za-z0-9]{24}"),
    ("Slack User Token", r"xoxp-[0-9]{10,}-[0-9]{10,}-[0-9]{10,}-[A-Za-z0-9]{32}"),
    ("Slack Webhook", r"https://hooks\.slack\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]+"),
    # SendGrid
    ("SendGrid Key", r"SG\.[A-Za-z0-9\-_]{22}\.[A-Za-z0-9\-_]{43}"),
    # Twilio
    ("Twilio Account SID", r"AC[a-f0-9]{32}"),
    ("Twilio Auth Token", r"(?i)twilio[_\-\s]?auth[_\-\s]?token\s*[=:]\s*['\"]?[a-f0-9]{32}['\"]?"),
    # PEM private keys (any type)
    ("Private Key", r"-----BEGIN (?:RSA|EC|DSA|OPENSSH|ENCRYPTED|PKCS8|) ?PRIVATE KEY-----"),
    # JDBC connection strings with credentials
    ("JDBC Connection String", r"jdbc:[a-z]+://[^/\s]*:[^/\s]*@"),
    # Generic password assignments (exclude obvious placeholders)
    ("Password Assignment", r"(?i)(?:password|passwd|pwd)\s*[=:]\s*['\"](?!your[_-]?password|example|placeholder|changeme|xxx+|<)[^'\"]{8,}['\"]"),
    # Generic bearer tokens
    ("Generic Bearer", r"[Bb]earer\s+[A-Za-z0-9\-._~+/]{40,}"),
]

# Pre-compiled for performance
_COMPILED = [(name, re.compile(pattern)) for name, pattern in SECRET_PATTERNS]


@dataclass
class SecretMatch:
    pattern_name: str
    line_number: int


def scan_for_secrets(content: str) -> list[SecretMatch]:
    matches = []
    for i, line in enumerate(content.splitlines(), start=1):
        for name, compiled in _COMPILED:
            if compiled.search(line):
                matches.append(SecretMatch(pattern_name=name, line_number=i))
                break  # One match per line is enough
    return matches


def redact_secrets(content: str) -> str:
    """Replace any line containing a secret with [REDACTED].

    Use for outbound doc_files/manifests/tree where blocking entirely would be
    too aggressive. Diffs use scan_for_secrets and abort instead.
    """
    lines = content.splitlines(keepends=True)
    result = []
    for line in lines:
        redacted = False
        for _name, compiled in _COMPILED:
            if compiled.search(line):
                ending = ""
                if line.endswith("\r\n"):
                    ending = "\r\n"
                elif line.endswith("\n"):
                    ending = "\n"
                elif line.endswith("\r"):
                    ending = "\r"
                result.append(f"[REDACTED]{ending}")
                redacted = True
                break
        if not redacted:
            result.append(line)
    return "".join(result)
