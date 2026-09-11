"""Redact credentials from URLs and other captured strings."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_USERINFO_RE = re.compile(r"^[^/@]+@")
_REDACTED = "[redacted]"
_SECRET_KEY_RE = re.compile(
    r"(token|password|passwd|secret|credential|authorization|api[_-]?key|"
    r"private[_-]?key|bearer|discord_webhook|webhook_url)",
    re.I,
)
_WEBHOOK_VALUE_RE = re.compile(
    r"https?://(?:(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\S+"
    r"|hooks\.slack\.com/\S+)",
    re.I,
)
_SAFE_FLAG_KEYS = frozenset(
    {
        "webhook_configured",
        "webhook_present",
        "webhook_absent",
    }
)


def redact_url(url: str | None) -> str | None:
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return "[unparseable-url]"
    netloc = parts.netloc
    if "@" in netloc:
        _, host = netloc.rsplit("@", 1)
        netloc = "***@" + host
    elif _USERINFO_RE.match(netloc):
        netloc = "***@" + netloc.split("@", 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _key_is_secret(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SAFE_FLAG_KEYS:
        return False
    if "webhook" in lowered:
        return True
    return bool(_SECRET_KEY_RE.search(key))


def sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WEBHOOK_VALUE_RE.sub(_REDACTED, value)
    if "://" in cleaned and " " not in cleaned.strip():
        cleaned = redact_url(cleaned) or cleaned
    return cleaned


def sanitize_captured(value: Any) -> Any:
    """Drop webhook URLs, tokens, and credentials. Keep boolean/tri-state flags."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _key_is_secret(name) and not isinstance(item, bool):
                out[name] = _REDACTED
            else:
                out[name] = sanitize_captured(item)
        return out
    if isinstance(value, list):
        return [sanitize_captured(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value
