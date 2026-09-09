"""Redact credentials from URLs and other captured strings."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_USERINFO_RE = re.compile(r"^[^/@]+@")


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
