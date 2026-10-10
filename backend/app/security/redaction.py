"""Centralised redaction used by logging, security events and storage.

Every component that needs to log, store or return operational metadata routes
it through this module so credentials and personal data cannot leak through a
log line by accident.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

REDACTED = "[redacted]"

# Credentials and tokens. Order matters: the scheme-qualified authorization
# form runs before the generic "authorization:" rule, which would otherwise
# consume only the word "Bearer" and leave the credential body in the output.
_PATTERNS: List[tuple] = [
    (re.compile(r"AIza[0-9A-Za-z\-_]{10,}"), REDACTED),
    (re.compile(r"gsk_[0-9A-Za-z_\-]{10,}"), REDACTED),
    (re.compile(r"(?i)\bauthorization\b\s*[:=]\s*(?:bearer|basic|digest|token)\s+[^\s\"',}]+"),
     f"authorization: {REDACTED}"),
    (re.compile(r"(?i)\bauthorization\b\s*[:=]\s*\"?[^\s\"',}]+"), f"authorization: {REDACTED}"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), f"Bearer {REDACTED}"),
    (re.compile(r"(?i)\b(?:x-api-key|api[_-]?key|apikey)\b\s*[:=]\s*\"?[^\s\"',}]+"), f"x-api-key: {REDACTED}"),
    (re.compile(r"(?i)\b(?:password|passwd|secret|token|private[_-]?key)\b\s*[:=]\s*\"?[^\s\"',}]+"), REDACTED),
    (re.compile(r"(?i)\bDATABASE_URL\b\s*[:=]\s*\S+"), f"DATABASE_URL: {REDACTED}"),
    (re.compile(r"postgres(?:ql)?://[^\s\"']+"), REDACTED),
    (re.compile(r"mysql(?:\+\w+)?://[^\s\"']+"), REDACTED),
]

# Personal data
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s\-()]{7,}\d(?![\w.])")

# Header names whose values must never be logged
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "api-key",
        "cookie",
        "set-cookie",
        "proxy-authorization",
        "x-auth-token",
    }
)

SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "private_key",
        "gemini_api_key",
        "groq_api_key",
        "google_api_key",
        "app_api_key",
        "database_url",
        "ssn",
        "card_number",
        "cvv",
    }
)


# Runtime secrets registered by the application. Values are matched literally
# so a credential that does not follow a known format (for example a custom
# shared API key) is still masked if it ever reaches a log line.
_RUNTIME_SECRETS: set = set()


def register_secret(value: Any) -> None:
    """Register a literal value that must never appear in output.

    Short values are ignored: masking a 1-3 character string would corrupt
    unrelated log text without protecting anything meaningful.
    """
    if value and isinstance(value, str) and len(value.strip()) >= 8:
        _RUNTIME_SECRETS.add(value.strip())


def redact(text: Any, limit: Optional[int] = None) -> str:
    """Mask credentials and personal data in a string."""
    if text is None:
        return ""
    cleaned = text if isinstance(text, str) else str(text)
    for secret in _RUNTIME_SECRETS:
        if secret in cleaned:
            cleaned = cleaned.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = EMAIL_RE.sub(REDACTED, cleaned)
    cleaned = PHONE_RE.sub(REDACTED, cleaned)
    if limit is not None and len(cleaned) > limit:
        cleaned = cleaned[:limit] + "...[truncated]"
    return cleaned


def safe_preview(text: Any, limit: int = 120) -> str:
    """Redacted, truncated representation of untrusted input (attack payloads)."""
    return redact(text, limit=limit)


def redact_value(value: Any, limit: Optional[int] = None) -> Any:
    """Recursively redact a structure, masking sensitive keys entirely."""
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in SENSITIVE_KEYS:
                result[key] = REDACTED
            else:
                result[key] = redact_value(item, limit=limit)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_value(item, limit=limit) for item in value]
    if isinstance(value, str):
        return redact(value, limit=limit)
    return value


def redact_headers(headers: Any) -> Dict[str, str]:
    """Return a loggable copy of request headers."""
    safe: Dict[str, str] = {}
    try:
        items: Iterable = headers.items()
    except AttributeError:
        return safe
    for key, value in items:
        name = str(key).lower()
        safe[name] = REDACTED if name in SENSITIVE_HEADERS else redact(value, limit=200)
    return safe


def mask_identifier(value: Optional[str], keep: int = 2) -> str:
    """Mask an identifier so logs stay useful without exposing the value."""
    if not value:
        return ""
    text = str(value)
    if len(text) <= keep:
        return "*" * len(text)
    return f"{text[:keep]}{'*' * (len(text) - keep)}"