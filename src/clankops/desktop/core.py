"""Desktop Alpha core: DB resolution, mutex, embedded Terminal, smoke.

This module must stay importable on Linux CI without pywebview.
It never opens a write-capable Store and never harvests, schedules
Pulse, or talks to GitHub.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from clankops.db import current_schema_version
from clankops.errors import ValidationError
from clankops.readmodel import ledger_fingerprint
from clankops.redact import sanitize_text
from clankops.schema import MIGRATIONS
from clankops.store import open_readonly_store
from clankops.terminal import serve

DESKTOP_MUTEX_NAME = r"Local\ClankOpsDesktop"
WINDOW_TITLE = "ClankOps"
WINDOW_WIDTH = 1440
WINDOW_HEIGHT = 900
MIN_WIDTH = 1000
MIN_HEIGHT = 650
INITIAL_PATH = "/"
WEBVIEW_GUI = "edgechromium"
TERMINAL_HOST = "127.0.0.1"
TERMINAL_PORT = 0
JOIN_TIMEOUT_SEC = 5.0
MAX_LOG_BYTES = 256 * 1024
KEEP_LOG_BYTES = 64 * 1024
ERROR_ALREADY_EXISTS = 183
WEBVIEW2_ERROR = (
    "ClankOps Desktop requires Microsoft Edge WebView2 Runtime. "
    "The CLI and clankctl terminal remain usable."
)

WEBVIEW_SETTINGS = {
    "ALLOW_DOWNLOADS": False,
    "ALLOW_FILE_URLS": False,
    "OPEN_EXTERNAL_LINKS_IN_BROWSER": True,
    "OPEN_DEVTOOLS_IN_DEBUG": False,
    "REMOTE_DEBUGGING_PORT": None,
    "SHOW_DEFAULT_MENUS": False,
}

WEBVIEW_START_KWARGS = {
    "gui": WEBVIEW_GUI,
    "debug": False,
    "private_mode": True,
    "http_server": False,
    "ssl": False,
}

_INPROC_MUTEXES: dict[str, int] = {}
_INPROC_LOCK = threading.Lock()


class DesktopError(Exception):
    """Visible desktop startup failure. Not a ledger event."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def default_db_path() -> Path:
    return Path.home() / ".clankops" / "clankops.db"


def resolve_db_path(
    explicit: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    if explicit is not None:
        text = str(explicit).strip()
        if not text:
            raise DesktopError("missing_db", "empty --db path")
        return Path(text).expanduser()
    env = os.environ if environ is None else environ
    env_db = str(env.get("CLANKOPS_DB") or "").strip()
    if env_db:
        return Path(env_db).expanduser()
    return default_db_path()


def initial_url(host: str, port: int) -> str:
    return f"http://{host}:{int(port)}{INITIAL_PATH}"


def window_kwargs(*, url: str, geometry: dict[str, Any] | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "title": WINDOW_TITLE,
        "url": url,
        "width": WINDOW_WIDTH,
        "height": WINDOW_HEIGHT,
        "min_size": (MIN_WIDTH, MIN_HEIGHT),
        "resizable": True,
        "frameless": False,
        "on_top": False,
        "confirm_close": False,
        "js_api": None,
        "text_select": True,
        "background_color": "#07090c",
        "hidden": False,
        "minimized": False,
        "maximized": False,
        "fullscreen": False,
    }
    if geometry:
        width = geometry.get("width")
        height = geometry.get("height")
        if isinstance(width, int) and width >= MIN_WIDTH:
            kwargs["width"] = width
        if isinstance(height, int) and height >= MIN_HEIGHT:
            kwargs["height"] = height
        x = geometry.get("x")
        y = geometry.get("y")
        if isinstance(x, int):
            kwargs["x"] = x
        if isinstance(y, int):
            kwargs["y"] = y
        if geometry.get("maximized") is True:
            kwargs["maximized"] = True
    return kwargs


def local_app_data() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def settings_path() -> Path:
    return local_app_data() / "ClankOps" / "desktop.json"


def log_path() -> Path:
    return local_app_data() / "ClankOps" / "logs" / "desktop.log"


def load_geometry(path: Path | None = None) -> dict[str, Any] | None:
    target = path or settings_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    cleaned: dict[str, Any] = {}
    for key in ("x", "y", "width", "height"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            cleaned[key] = value
        elif isinstance(value, float) and value.is_integer():
            cleaned[key] = int(value)
    if "maximized" in raw:
        cleaned["maximized"] = bool(raw.get("maximized"))
    if not cleaned:
        return None
    return cleaned


def save_geometry(payload: Mapping[str, Any], path: Path | None = None) -> None:
    target = path or settings_path()
    body = {
        "x": payload.get("x"),
        "y": payload.get("y"),
        "width": payload.get("width"),
        "height": payload.get("height"),
        "maximized": bool(payload.get("maximized")),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        log_desktop("warn", f"could not save window geometry: {exc}")


def log_desktop(level: str, message: str) -> None:
    text = sanitize_text(str(message)) or ""
    line = f"{level} {text}\n"
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > MAX_LOG_BYTES:
            kept = path.read_bytes()[-KEEP_LOG_BYTES:]
            path.write_bytes(b"...truncated...\n" + kept)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def expected_ledger_schema_version() -> int:
    return max(version for version, _name, _sql in MIGRATIONS)


def validate_readonly_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise DesktopError(
            "missing_db",
            (
                f"ClankOps Desktop could not find a ledger at {path}. "
                "Desktop does not create a database. Initialize with "
                "clankctl init or pass --db."
            ),
        )
    if not path.is_file():
        raise DesktopError(
            "unreadable_db",
            f"ClankOps Desktop could not open the ledger at {path} for reading.",
        )
    try:
        store = open_readonly_store(path)
    except ValidationError as exc:
        detail = str(exc)
        code = "missing_db" if "not found" in detail.lower() else "unreadable_db"
        raise DesktopError(
            code,
            (
                f"ClankOps Desktop could not find a ledger at {path}. "
                "Desktop does not create a database."
                if code == "missing_db"
                else f"ClankOps Desktop could not open the ledger at {path} for reading."
            ),
        ) from exc
    except Exception as exc:
        raise DesktopError(
            "unreadable_db",
            f"ClankOps Desktop could not open the ledger at {path} for reading.",
        ) from exc
    try:
        try:
            version = current_schema_version(store.conn)
        except Exception as exc:
            raise DesktopError(
                "unreadable_db",
                f"ClankOps Desktop could not read the ledger schema at {path}.",
            ) from exc
        expected = expected_ledger_schema_version()
        if version != expected:
            raise DesktopError(
                "incompatible_schema",
                (
                    "ClankOps Desktop found an incompatible ledger schema "
                    f"(found {version}, expected {expected}) at {path}."
                ),
            )
        fingerprint = ledger_fingerprint(store)
        return {
            "path": str(path),
            "schema_version": version,
            "event_count": fingerprint["event_count"],
            "max_ledger_seq": fingerprint["max_ledger_seq"],
        }
    finally:
        store.conn.close()


class SingleInstance:
    """Windows named mutex, with an in-process fallback for Linux tests."""

    def __init__(self, name: str = DESKTOP_MUTEX_NAME) -> None:
        self.name = name
        self.acquired = False
        self._handle = None

    def acquire(self) -> bool:
        if self.acquired:
            return True
        if sys.platform == "win32":
            ok = self._acquire_windows()
        else:
            ok = self._acquire_inproc()
        self.acquired = ok
        return ok

    def release(self) -> None:
        if not self.acquired:
            return
        if sys.platform == "win32":
            self._release_windows()
        else:
            self._release_inproc()
        self.acquired = False

    def _acquire_windows(self) -> bool:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, True, self.name)
        if not handle:
            return False
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def _release_windows(self) -> None:
        import ctypes

        handle = self._handle
        self._handle = None
        if not handle:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)

    def _acquire_inproc(self) -> bool:
        with _INPROC_LOCK:
            if self.name in _INPROC_MUTEXES:
                return False
            _INPROC_MUTEXES[self.name] = threading.get_ident()
            return True

    def _release_inproc(self) -> None:
        with _INPROC_LOCK:
            _INPROC_MUTEXES.pop(self.name, None)

    def __enter__(self) -> "SingleInstance":
        if not self.acquire():
            raise DesktopError("already_running", "ClankOps is already running.")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class EmbeddedTerminal:
    """Own a `clankops.terminal.serve()` instance on loopback port 0."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.host = TERMINAL_HOST
        self.port = 0
        self._lock = threading.Lock()
        self._stopped = False

    @property
    def url(self) -> str:
        return initial_url(self.host, self.port)

    def start(self) -> str:
        try:
            server = serve(
                db_path=self.db_path,
                host=TERMINAL_HOST,
                port=TERMINAL_PORT,
                allow_remote=False,
            )
        except ValidationError as exc:
            raise DesktopError(
                "bind_failed",
                f"ClankOps Desktop could not start the local Terminal server: {exc}",
            ) from exc
        except OSError as exc:
            raise DesktopError(
                "bind_failed",
                "ClankOps Desktop could not start the local Terminal server.",
            ) from exc
        bound_host, bound_port = server.server_address
        self.server = server
        self.host = str(bound_host)
        self.port = int(bound_port)
        server.daemon_threads = True
        server.block_on_close = False
        if self.port <= 0:
            self.stop()
            raise DesktopError(
                "bind_failed",
                "ClankOps Desktop could not start the local Terminal server.",
            )
        thread = threading.Thread(
            target=server.serve_forever,
            name="clankops-desktop-terminal",
            daemon=True,
        )
        self.thread = thread
        thread.start()
        return self.url

    def stop(self, timeout: float = JOIN_TIMEOUT_SEC) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            server = self.server
            thread = self.thread
        if server is not None:
            try:
                server.shutdown()
            except Exception as exc:
                log_desktop("warn", f"terminal shutdown: {exc}")
            try:
                server.server_close()
            except Exception as exc:
                log_desktop("warn", f"terminal server_close: {exc}")
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                log_desktop("warn", "terminal thread still alive after join timeout")


def http_get(url: str, timeout: float = 5.0) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            return int(response.status), headers, response.read()
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return int(exc.code), headers, exc.read()


def run_smoke_test(
    db_path: Path,
    output: Path,
    *,
    webview: bool = False,
) -> dict[str, Any]:
    info = validate_readonly_ledger(db_path)
    terminal = EmbeddedTerminal(db_path)
    result: dict[str, Any] = {
        "ok": False,
        "host": TERMINAL_HOST,
        "port": 0,
        "status": None,
        "mode": None,
        "url": None,
        "event_count": info["event_count"],
        "max_ledger_seq": info["max_ledger_seq"],
        "schema_version": info["schema_version"],
        "renderer": None,
        "webview_loaded": False,
    }
    try:
        url = terminal.start()
        result["host"] = terminal.host
        result["port"] = terminal.port
        result["url"] = url
        status, headers, _body = http_get(url)
        result["status"] = status
        result["mode"] = headers.get("x-clankops-mode")
        if status != 200 or result["mode"] != "read-only":
            raise DesktopError(
                "bind_failed",
                "ClankOps Desktop smoke test did not receive a read-only Terminal home page.",
            )
        if webview:
            from clankops.desktop.webview_host import run_hidden_webview_smoke

            view = run_hidden_webview_smoke(url)
            result["renderer"] = view.get("renderer")
            result["webview_loaded"] = bool(view.get("loaded"))
            if result["renderer"] != WEBVIEW_GUI or not result["webview_loaded"]:
                raise DesktopError(
                    "webview2_unavailable",
                    WEBVIEW2_ERROR,
                )
        result["ok"] = True
        return result
    finally:
        terminal.stop()
        after = validate_readonly_ledger(db_path)
        result["event_count_after"] = after["event_count"]
        result["max_ledger_seq_after"] = after["max_ledger_seq"]
        if after["event_count"] != info["event_count"] or after["max_ledger_seq"] != info[
            "max_ledger_seq"
        ]:
            result["ok"] = False
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def _user_message(exc: BaseException) -> str:
    if isinstance(exc, DesktopError):
        return str(exc)
    return (
        "ClankOps Desktop failed to start. See "
        f"{log_path()} for diagnostics."
    )


def _safe_stdio_write(stream, text: str) -> None:
    if stream is None:
        return
    try:
        stream.write(text)
        stream.flush()
    except OSError:
        pass


def show_startup_error(message: str, *, dialog: bool = True) -> None:
    text = message if message.endswith("\n") else message + "\n"
    _safe_stdio_write(sys.stderr, text)
    if not dialog:
        return
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            message,
            "ClankOps",
            0x00000010 | 0x00010000,
        )
    except Exception:
        pass


def run_desktop(
    *,
    db_path: Path,
    window_runner: Any | None = None,
    mutex_name: str = DESKTOP_MUTEX_NAME,
) -> int:
    guard = SingleInstance(mutex_name)
    if not guard.acquire():
        raise DesktopError("already_running", "ClankOps is already running.")
    terminal: EmbeddedTerminal | None = None
    try:
        validate_readonly_ledger(db_path)
        terminal = EmbeddedTerminal(db_path)
        url = terminal.start()
        log_desktop("info", f"embedded terminal {url}")
        runner = window_runner
        if runner is None:
            from clankops.desktop.webview_host import run_native_window

            runner = run_native_window
        runner(url)
        return 0
    except Exception:
        if terminal is not None:
            terminal.stop()
        raise
    finally:
        if terminal is not None:
            terminal.stop()
        guard.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clankops-desktop",
        description="Native Windows shell for the ClankOps Terminal (read-only).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite ledger path (default: CLANKOPS_DB or %%USERPROFILE%%/.clankops/clankops.db)",
    )
    parser.add_argument(
        "--smoke-test",
        metavar="OUTPUT_JSON",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--webview-smoke",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def _finish(code: int) -> int:
    """Windowed PyInstaller builds can retain WinForms/pythonnet threads."""
    if getattr(sys, "frozen", False):
        os._exit(int(code))
    return int(code)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    dialog = not bool(args.smoke_test)
    try:
        db_path = resolve_db_path(args.db)
        if args.smoke_test:
            output = Path(args.smoke_test)
            result = run_smoke_test(
                db_path,
                output,
                webview=bool(args.webview_smoke),
            )
            _safe_stdio_write(sys.stdout, json.dumps(result, indent=2) + "\n")
            return _finish(0 if result.get("ok") else 2)
        return _finish(run_desktop(db_path=db_path))
    except DesktopError as exc:
        log_desktop("error", f"{exc.code}: {exc}")
        show_startup_error(str(exc), dialog=dialog)
        return _finish(2)
    except Exception as exc:
        log_desktop("error", f"unexpected: {type(exc).__name__}: {exc}")
        show_startup_error(_user_message(exc), dialog=dialog)
        return _finish(2)
