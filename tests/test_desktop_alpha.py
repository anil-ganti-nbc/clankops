"""Desktop Alpha: native shell around Terminal Beta. No GUI required."""

from __future__ import annotations

import ast
import json
import socket
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from clankops.cli import main as cli_main
from clankops.desktop import (
    DESKTOP_MUTEX_NAME,
    INITIAL_PATH,
    WEBVIEW_GUI,
    WEBVIEW_SETTINGS,
    WEBVIEW_START_KWARGS,
    WINDOW_TITLE,
    DesktopError,
    EmbeddedTerminal,
    SingleInstance,
    default_db_path,
    initial_url,
    main as desktop_main,
    resolve_db_path,
    run_desktop,
    run_smoke_test,
    validate_readonly_ledger,
    window_kwargs,
)
from clankops.desktop.core import log_path, run_desktop as run_desktop_core
from clankops.desktop.webview_host import apply_webview_settings
from clankops.readmodel import ledger_fingerprint
from clankops.store import open_readonly_store, open_store

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_DIR = ROOT / "src" / "clankops" / "desktop"


def _desktop_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(DESKTOP_DIR.glob("*.py")))


def _seed_db(path: Path) -> None:
    store = open_store(path, actor="tester")
    store.register_clank("oem-radar", display_name="OEM Radar")
    store.conn.close()


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def test_db_resolution_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "explicit.db"
    env_db = tmp_path / "from-env.db"
    monkeypatch.setenv("CLANKOPS_DB", str(env_db))
    assert resolve_db_path(explicit) == explicit
    assert resolve_db_path(None) == env_db
    assert resolve_db_path(None, {"CLANKOPS_DB": str(env_db)}) == env_db
    assert resolve_db_path(None, {}) == default_db_path()
    assert default_db_path() == Path.home() / ".clankops" / "clankops.db"


def test_missing_db_rejected_and_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "absent" / "clankops.db"
    with pytest.raises(DesktopError, match="could not find") as exc:
        validate_readonly_ledger(missing)
    assert exc.value.code == "missing_db"
    assert not missing.exists()
    assert not missing.parent.exists()


def test_read_only_startup_leaves_event_count_unchanged(db_path: Path, store) -> None:
    store.register_clank("oem-radar", display_name="OEM Radar")
    before = ledger_fingerprint(store)
    info = validate_readonly_ledger(db_path)
    after = ledger_fingerprint(store)
    assert info["event_count"] == before["event_count"]
    assert info["max_ledger_seq"] == before["max_ledger_seq"]
    assert after == before


def test_embedded_terminal_binds_loopback_ephemeral(db_path: Path) -> None:
    _seed_db(db_path)
    terminal = EmbeddedTerminal(db_path)
    try:
        url = terminal.start()
        assert terminal.host in {"127.0.0.1", "localhost"}
        assert terminal.port > 0
        assert INITIAL_PATH == "/"
        assert url == initial_url(terminal.host, terminal.port)
        assert url.endswith("/")
        assert "?live" not in url
        assert "github" not in url
        status, headers, _body = _get(url)
        assert status == 200
        assert headers.get("x-clankops-mode") == "read-only"
    finally:
        terminal.stop()


def test_server_lifecycle_starts_and_stops_thread(db_path: Path) -> None:
    _seed_db(db_path)
    terminal = EmbeddedTerminal(db_path)
    url = terminal.start()
    assert terminal.thread is not None
    assert terminal.thread.is_alive()
    assert _port_open(terminal.host, terminal.port)
    terminal.stop()
    assert not terminal.thread.is_alive()
    assert not _port_open(terminal.host, terminal.port)
    with pytest.raises(OSError):
        _get(url)


def test_startup_failure_after_bind_cleans_server(db_path: Path) -> None:
    _seed_db(db_path)
    seen: dict[str, int] = {}

    def boom(url: str) -> None:
        host, port_s = url.rsplit(":", 1)
        seen["port"] = int(port_s.rstrip("/"))
        seen["host"] = host.split("://", 1)[-1]
        raise DesktopError("webview2_unavailable", "forced")

    name = rf"Local\ClankOpsDesktopTest-{uuid.uuid4()}"
    with pytest.raises(DesktopError, match="forced"):
        run_desktop(db_path=db_path, window_runner=boom, mutex_name=name)
    assert seen["port"] > 0
    assert not _port_open(seen["host"], seen["port"])


def test_single_instance_acquire_release() -> None:
    name = rf"Local\ClankOpsDesktopTest-{uuid.uuid4()}"
    first = SingleInstance(name)
    second = SingleInstance(name)
    third = SingleInstance(name)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert third.acquire() is True
    third.release()


def test_desktop_import_without_pywebview() -> None:
    import clankops
    import clankops.cli
    import clankops.desktop.core as core

    source = Path(core.__file__).read_text(encoding="utf-8")
    assert "import webview" not in source
    assert "from webview" not in source
    with pytest.raises(SystemExit) as exc:
        cli_main(["--help"])
    assert exc.value.code == 0
    assert clankops.__version__


def test_help_uses_system_exit() -> None:
    with pytest.raises(SystemExit) as exc:
        desktop_main(["--help"])
    assert exc.value.code == 0


def test_no_write_capable_store_or_harvest_or_pulse_or_js_api() -> None:
    source = _desktop_source()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            for alias in node.names:
                imported.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
    assert "clankops.harvest" not in imported
    assert "clankops.pulse" not in imported
    assert "clankops.gitinspect" not in imported
    assert "clankops.githubinspect" not in imported
    assert "clankops.store.open_store" not in imported
    assert "open_store(" not in source.replace("open_readonly_store(", "")
    assert "harvest_local_git" not in source
    assert "plan_install" not in source
    assert "inspect_github" not in source
    assert "js_api=" not in source.replace("js_api=None", "")
    assert "window.pywebview" not in source


def test_webview_requested_edgechromium_conservative_settings() -> None:
    assert WEBVIEW_GUI == "edgechromium"
    assert WEBVIEW_START_KWARGS["gui"] == "edgechromium"
    assert WEBVIEW_START_KWARGS["debug"] is False
    assert WEBVIEW_SETTINGS["ALLOW_DOWNLOADS"] is False
    assert WEBVIEW_SETTINGS["ALLOW_FILE_URLS"] is False
    assert WEBVIEW_SETTINGS["OPEN_DEVTOOLS_IN_DEBUG"] is False
    assert WEBVIEW_SETTINGS["REMOTE_DEBUGGING_PORT"] is None
    fake = SimpleNamespace(
        settings={
            "ALLOW_DOWNLOADS": True,
            "ALLOW_FILE_URLS": True,
            "OPEN_EXTERNAL_LINKS_IN_BROWSER": False,
            "OPEN_DEVTOOLS_IN_DEBUG": True,
            "REMOTE_DEBUGGING_PORT": 9222,
            "SHOW_DEFAULT_MENUS": True,
        }
    )
    apply_webview_settings(fake)
    assert fake.settings["ALLOW_DOWNLOADS"] is False
    assert fake.settings["ALLOW_FILE_URLS"] is False
    assert fake.settings["REMOTE_DEBUGGING_PORT"] is None
    assert fake.settings["OPEN_DEVTOOLS_IN_DEBUG"] is False
    kwargs = window_kwargs(url="http://127.0.0.1:9/")
    assert kwargs["js_api"] is None
    assert kwargs["title"] == WINDOW_TITLE
    assert kwargs["frameless"] is False
    assert kwargs["on_top"] is False
    assert kwargs["confirm_close"] is False
    assert "?" not in kwargs["url"]
    assert DESKTOP_MUTEX_NAME == r"Local\ClankOpsDesktop"


def test_incompatible_schema_rejected(db_path: Path) -> None:
    _seed_db(db_path)
    store = open_store(db_path, actor="tester")
    store.conn.execute(
        "INSERT INTO schema_migrations(version, name, applied_at_utc) VALUES (99, 'future', '2026-01-01T00:00:00Z')"
    )
    store.conn.commit()
    store.conn.close()
    with pytest.raises(DesktopError) as exc:
        validate_readonly_ledger(db_path)
    assert exc.value.code == "incompatible_schema"
    assert db_path.is_file()


def test_unreadable_db_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "garbage.db"
    bad.write_bytes(b"not a sqlite database")
    with pytest.raises(DesktopError) as exc:
        validate_readonly_ledger(bad)
    assert exc.value.code == "unreadable_db"


def test_smoke_test_http_and_no_ledger_write(db_path: Path, tmp_path: Path) -> None:
    _seed_db(db_path)
    reader = open_readonly_store(db_path)
    before = ledger_fingerprint(reader)
    reader.conn.close()
    output = tmp_path / "smoke.json"
    result = run_smoke_test(db_path, output, webview=False)
    assert result["ok"] is True
    assert result["status"] == 200
    assert result["mode"] == "read-only"
    assert result["port"] > 0
    assert result["url"].endswith("/")
    assert "?live" not in result["url"]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["event_count"] == before["event_count"]
    assert payload["event_count_after"] == before["event_count"]
    assert payload["max_ledger_seq_after"] == before["max_ledger_seq"]


def test_smoke_cli_writes_json(db_path: Path, tmp_path: Path) -> None:
    _seed_db(db_path)
    output = tmp_path / "cli-smoke.json"
    code = desktop_main(["--db", str(db_path), "--smoke-test", str(output)])
    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["mode"] == "read-only"


def test_core_packaging_stays_dependency_light() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in text
    assert 'desktop = ["pywebview==6.2.1"]' in text
    assert "pywebview" not in text.split("[project.optional-dependencies]")[0]
    assert "pythonnet" not in text.split("[project.optional-dependencies]")[0]


def test_startup_does_not_call_harvest_pulse_or_github(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_db(db_path)

    def boom(*_a, **_k):
        raise AssertionError("forbidden desktop call")

    monkeypatch.setattr("clankops.harvest.harvest_local_git", boom)
    monkeypatch.setattr("clankops.pulse.plan_install", boom)
    monkeypatch.setattr("clankops.githubinspect.inspect_github", boom)
    monkeypatch.setattr("clankops.gitinspect.inspect_git", boom)
    terminal = EmbeddedTerminal(db_path)
    try:
        url = terminal.start()
        status, headers, _ = _get(url)
        assert status == 200
        assert headers.get("x-clankops-mode") == "read-only"
    finally:
        terminal.stop()


def test_corrupt_geometry_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    from clankops.desktop.core import load_geometry, settings_path

    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_geometry() is None


def test_log_path_is_localappdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert log_path() == tmp_path / "local" / "ClankOps" / "logs" / "desktop.log"


def _get(url: str):
    from clankops.desktop.core import http_get

    return http_get(url)


def test_run_desktop_exported() -> None:
    assert run_desktop is run_desktop_core
