"""Read-only Clank Terminal (Foundation 2 alpha).

Localhost HTML/JSON over projection tables. No mutations, no scheduler,
no remote bind by default.
"""

from __future__ import annotations

import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from clankops.brief import format_brief
from clankops.errors import NotFoundError
from clankops.store import Store

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _json(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n").encode(
        "utf-8"
    )


def dispatch(store: Store, method: str, path: str) -> tuple[int, str, bytes]:
    """Pure request handler used by tests and the HTTP wrapper."""
    method = method.upper()
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    if method not in {"GET", "HEAD"}:
        return HTTPStatus.METHOD_NOT_ALLOWED, "text/plain; charset=utf-8", b"read-only\n"
    try:
        if route in {"/", "/fleet"}:
            return HTTPStatus.OK, "text/html; charset=utf-8", _fleet_html(store).encode("utf-8")
        if route == "/api/fleet":
            return HTTPStatus.OK, "application/json; charset=utf-8", _json(store.fleet_overview())
        if route == "/health":
            return HTTPStatus.OK, "application/json; charset=utf-8", _json({"ok": True, "mode": "read-only"})
        if route.startswith("/api/clank/"):
            slug = unquote(route.removeprefix("/api/clank/"))
            return HTTPStatus.OK, "application/json; charset=utf-8", _json(store.brief(slug))
        if route.startswith("/clank/"):
            slug = unquote(route.removeprefix("/clank/"))
            brief = store.brief(slug)
            return HTTPStatus.OK, "text/html; charset=utf-8", _brief_html(brief).encode("utf-8")
    except NotFoundError as exc:
        return HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", f"{exc}\n".encode("utf-8")
    return HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n"


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{ font-family: ui-monospace, Consolas, monospace; margin: 1.5rem; background: #0b0f14; color: #d7e0ea; }}
    a {{ color: #7ec8e3; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #243040; text-align: left; padding: 0.4rem 0.6rem; vertical-align: top; }}
    .muted {{ color: #8aa0b5; }}
    pre {{ white-space: pre-wrap; }}
    header {{ margin-bottom: 1rem; }}
  </style>
</head>
<body>
  <header>
    <strong>ClankOps Terminal</strong>
    <span class="muted"> read-only alpha · localhost</span>
    <div><a href="/">fleet</a> · <a href="/api/fleet">api/fleet</a></div>
  </header>
  {body}
</body>
</html>
"""


def _fleet_html(store: Store) -> str:
    rows = store.fleet_overview()
    cells = []
    for row in rows:
        slug = html.escape(row["slug"])
        mission = html.escape(row.get("mission_display") or "—")
        state = html.escape(row.get("mission_state") or "—")
        objective = html.escape(row.get("mission_objective") or "")
        path = html.escape(row.get("local_path") or "—")
        cells.append(
            "<tr>"
            f"<td><a href=\"/clank/{slug}\">{slug}</a></td>"
            f"<td>{html.escape(row.get('display_name') or slug)}</td>"
            f"<td>{html.escape(row.get('lifecycle') or '')}</td>"
            f"<td>{mission} [{state}]</td>"
            f"<td>{objective}</td>"
            f"<td class=\"muted\">{path}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr>"
        "<th>slug</th><th>name</th><th>lifecycle</th><th>mission</th><th>objective</th><th>path</th>"
        "</tr></thead><tbody>"
        + "".join(cells)
        + "</tbody></table>"
    )
    return _page("ClankOps Terminal", f"<p class=\"muted\">{len(rows)} Clanks</p>{table}")


def _brief_html(brief: dict[str, Any]) -> str:
    slug = brief["identity"]["slug"]
    body = f"<h1>{html.escape(brief['identity']['display_name'])}</h1><pre>{html.escape(format_brief(brief))}</pre>"
    return _page(f"{slug} · ClankOps Terminal", body)


class TerminalHandler(BaseHTTPRequestHandler):
    store: Store

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _write(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        status, ctype, body = dispatch(self.store, "GET", self.path)
        self._write(status, ctype, body)

    def do_HEAD(self) -> None:  # noqa: N802
        status, ctype, body = dispatch(self.store, "HEAD", self.path)
        self._write(status, ctype, body)

    def do_POST(self) -> None:  # noqa: N802
        status, ctype, body = dispatch(self.store, "POST", self.path)
        self._write(status, ctype, body)


def serve(store: Store, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    handler = type("BoundTerminalHandler", (TerminalHandler,), {"store": store})
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd
