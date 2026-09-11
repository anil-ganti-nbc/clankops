"""Redact credentials from URLs and other captured strings.

Webhook URLs, tokens, passwords and credentials must never enter the ledger.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_USERINFO_RE = re.compile(r"^[^/@]+@")
_REDACTED = "[redacted]"
_SECRET_KEY_RE = re.compile(
    r"(token|password|passwd|secret|credential|authorization|api[_-]?key|"
    r"private[_-]?key|bearer|discord_webhook|webhook_url)",
    re.I,
)
_SECRET_PARAM_RE = re.compile(
    r"(token|password|passwd|secret|credential|authorization|api[_-]?key|"
    r"access[_-]?token|bearer)",
    re.I,
)
_WEBHOOK_VALUE_RE = re.compile(
    r"https?://(?:(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\S+"
    r"|hooks\.slack\.com/\S+)",
    re.I,
)
_URL_RE = re.compile(r"https?://[^\s]+", re.I)
_BEARER_RE = re.compile(r"\bBearer\s+\S+", re.I)
_GITHUB_TOKEN_RE = re.compile(
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_\-]{8,}\b"
    r"|\bgithub_pat_[A-Za-z0-9_\-]{8,}\b",
    re.I,
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&#])(token|password|passwd|secret|api[_-]?key|access[_-]?token|bearer)=([^\s&#]+)"
)
_SAFE_FLAG_KEYS = frozenset(
    {
        "webhook_configured",
        "webhook_present",
        "webhook_absent",
    }
)
_SAFE_FLAG_VALUES = frozenset({"yes", "no", "unknown", "true", "false"})


def _is_secret_param(name: str) -> bool:
    return bool(_SECRET_PARAM_RE.search(name))


def _redact_query(query: str) -> str:
    if not query or "=" not in query:
        return query
    pairs = parse_qsl(query, keep_blank_values=True)
    redacted = [
        (key, _REDACTED if _is_secret_param(key) else value) for key, value in pairs
    ]
    return urlencode(redacted)


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
    query = _redact_query(parts.query)
    fragment = parts.fragment
    if fragment and "=" in fragment:
        fragment = _redact_query(fragment)
    return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))


def _key_is_secret(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SAFE_FLAG_KEYS:
        return False
    if "webhook" in lowered:
        return True
    return bool(_SECRET_KEY_RE.search(key))


def _redact_urls_in_text(text: str) -> str:
    def _one(match: re.Match[str]) -> str:
        return redact_url(match.group(0)) or match.group(0)

    return _URL_RE.sub(_one, text)


def sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WEBHOOK_VALUE_RE.sub(_REDACTED, value)
    cleaned = _redact_urls_in_text(cleaned)
    if "://" in cleaned and " " not in cleaned.strip():
        cleaned = redact_url(cleaned) or cleaned
    cleaned = _BEARER_RE.sub(f"Bearer {_REDACTED}", cleaned)
    cleaned = _GITHUB_TOKEN_RE.sub(_REDACTED, cleaned)
    cleaned = _QUERY_SECRET_RE.sub(rf"\1\2={_REDACTED}", cleaned)
    return cleaned


def sanitize_captured(value: Any) -> Any:
    """Drop webhook URLs, tokens, and credentials. Keep boolean/tri-state flags."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _key_is_secret(name) and not _keep_flag(item):
                out[name] = _REDACTED
            else:
                out[name] = sanitize_captured(item)
        return out
    if isinstance(value, list):
        return [sanitize_captured(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def _keep_flag(item: Any) -> bool:
    if isinstance(item, bool):
        return True
    if isinstance(item, str) and item.strip().lower() in _SAFE_FLAG_VALUES:
        return True
    return False
