from docdr.scanner import SecretMatch, redact_secrets, scan_for_secrets

CLEAN_CONTENT = """\
def hello():
    name = "world"
    return f"hello {name}"
"""

CONTENT_WITH_PAT = """\
# config
token = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456789012"
"""

CONTENT_WITH_AWS = """\
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
"""

CONTENT_WITH_PRIVATE_KEY = """\
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA...
-----END RSA PRIVATE KEY-----
"""


# --- existing tests ---

def test_clean_content_has_no_secrets():
    matches = scan_for_secrets(CLEAN_CONTENT)
    assert matches == []


def test_detects_github_pat():
    matches = scan_for_secrets(CONTENT_WITH_PAT)
    assert any(m.pattern_name == "GitHub PAT" for m in matches)


def test_detects_aws_key():
    matches = scan_for_secrets(CONTENT_WITH_AWS)
    assert any(m.pattern_name == "AWS Access Key" for m in matches)


def test_detects_private_key():
    matches = scan_for_secrets(CONTENT_WITH_PRIVATE_KEY)
    assert any(m.pattern_name == "Private Key" for m in matches)


def test_match_includes_line_number():
    matches = scan_for_secrets(CONTENT_WITH_PAT)
    assert matches[0].line_number == 2


# --- new pattern tests ---

def _build_aws_secret():
    # Construct at runtime to avoid triggering push protection
    prefix = "aws_secret_access_key = '"
    key = "a" * 20 + "B" * 10 + "c" * 10
    return prefix + key + "'"


def test_detects_aws_secret_key():
    content = _build_aws_secret()
    matches = scan_for_secrets(content)
    assert any(m.pattern_name == "AWS Secret Key" for m in matches)


def test_detects_gcp_service_account():
    content = '{"type": "service_account", "project_id": "my-project"}'
    matches = scan_for_secrets(content)
    assert any(m.pattern_name == "GCP Service Account" for m in matches)


def test_detects_stripe_restricted_key():
    # rk_live_ prefix
    key = "rk_live_" + "A" * 30
    matches = scan_for_secrets(key)
    assert any(m.pattern_name == "Stripe Restricted Key" for m in matches)


def _build_slack_bot_token():
    # xoxb-{11digits}-{11digits}-{24alphanum}
    return "xoxb-" + "1" * 11 + "-" + "2" * 11 + "-" + "A" * 24


def test_detects_slack_bot_token():
    content = "SLACK_TOKEN=" + _build_slack_bot_token()
    matches = scan_for_secrets(content)
    assert any(m.pattern_name == "Slack Bot Token" for m in matches)


def test_detects_slack_webhook():
    url = "https://hooks.slack.com/services/TABCDEF123/BABCDEF456/xyzXYZ123abc"
    matches = scan_for_secrets(url)
    assert any(m.pattern_name == "Slack Webhook" for m in matches)


def _build_sendgrid_key():
    # SG.{22}.{43}
    part1 = "A" * 22
    part2 = "B" * 43
    return f"SG.{part1}.{part2}"


def test_detects_sendgrid_key():
    content = "SENDGRID_API_KEY=" + _build_sendgrid_key()
    matches = scan_for_secrets(content)
    assert any(m.pattern_name == "SendGrid Key" for m in matches)


def test_detects_twilio_account_sid():
    # AC + 32 hex chars
    sid = "AC" + "a" * 32
    matches = scan_for_secrets(sid)
    assert any(m.pattern_name == "Twilio Account SID" for m in matches)


def test_detects_twilio_auth_token():
    token_val = "f" * 32
    content = f"twilio_auth_token = '{token_val}'"
    matches = scan_for_secrets(content)
    assert any(m.pattern_name == "Twilio Auth Token" for m in matches)


def test_detects_jdbc_connection_string():
    url = "jdbc:postgresql://user:s3cr3tpassword@db.example.com:5432/mydb"
    matches = scan_for_secrets(url)
    assert any(m.pattern_name == "JDBC Connection String" for m in matches)


def test_detects_password_assignment():
    content = 'password = "mySuperSecret123"'
    matches = scan_for_secrets(content)
    assert any(m.pattern_name == "Password Assignment" for m in matches)


def test_ignores_placeholder_password():
    content = 'password = "changeme"'
    matches = scan_for_secrets(content)
    assert not any(m.pattern_name == "Password Assignment" for m in matches)


def test_detects_pem_private_key_ec():
    content = "-----BEGIN EC PRIVATE KEY-----\nMHQCAQEEI...\n-----END EC PRIVATE KEY-----"
    matches = scan_for_secrets(content)
    assert any(m.pattern_name == "Private Key" for m in matches)


# --- redact_secrets tests ---

def test_redact_replaces_secret_line():
    content = 'token = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456789012"\nclean line\n'
    result = redact_secrets(content)
    lines = result.splitlines()
    assert lines[0] == "[REDACTED]"
    assert lines[1] == "clean line"


def test_redact_preserves_clean_lines():
    result = redact_secrets(CLEAN_CONTENT)
    assert result == CLEAN_CONTENT


def test_redact_preserves_newlines():
    content = "clean\n" + "AKIAIOSFODNN7EXAMPLE\n" + "also clean\n"
    result = redact_secrets(content)
    assert result == "clean\n[REDACTED]\nalso clean\n"


def test_redact_multiline_secret_block():
    pat_line = "-----BEGIN RSA PRIVATE KEY-----\n"
    content = "before\n" + pat_line + "key data\n" + "after\n"
    result = redact_secrets(content)
    lines = result.splitlines()
    assert lines[0] == "before"
    assert lines[1] == "[REDACTED]"
    # key data line is clean (no pattern), after line is clean
    assert lines[2] == "key data"
    assert lines[3] == "after"
