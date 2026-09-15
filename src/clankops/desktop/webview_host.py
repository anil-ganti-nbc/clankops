"""Lazy pywebview host. Importing this module requires the desktop extra."""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

from clankops.desktop.core import (
    WEBVIEW2_ERROR,
    WEBVIEW_GUI,
    WEBVIEW_SETTINGS,
    WEBVIEW_START_KWARGS,
    DesktopError,
    load_geometry,
    log_desktop,
    save_geometry,
    window_kwargs,
)

MINIMAL_SMOKE_WIDTH = 1000
MINIMAL_SMOKE_HEIGHT = 650
WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_KEYS = (
    WEBVIEW2_CLIENT,
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",
)


def apply_webview_settings(module: Any) -> None:
    for key, value in WEBVIEW_SETTINGS.items():
        module.settings[key] = value


def webview2_runtime_version() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    roots = (
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
    )
    for hive, prefix in roots:
        for client in WEBVIEW2_KEYS:
            try:
                key = winreg.OpenKey(hive, prefix + "\\" + client)
            except OSError:
                continue
            try:
                version, _ = winreg.QueryValueEx(key, "pv")
            except OSError:
                version = None
            finally:
                winreg.CloseKey(key)
            if version and str(version) not in {"", "0.0.0.0"}:
                return str(version)
    return None


def require_webview2() -> str:
    version = webview2_runtime_version()
    if not version:
        raise DesktopError("webview2_unavailable", WEBVIEW2_ERROR)
    return version


def _load_webview() -> Any:
    try:
        import webview
    except ImportError as exc:
        raise DesktopError("webview2_unavailable", WEBVIEW2_ERROR) from exc
    apply_webview_settings(webview)
    return webview


def _assert_edgechromium(renderer: str | None) -> None:
    if renderer != WEBVIEW_GUI:
        raise DesktopError("webview2_unavailable", WEBVIEW2_ERROR)


def _geometry_from_window(window: Any) -> dict[str, Any]:
    def _val(name: str) -> Any:
        try:
            raw = getattr(window, name, None)
            if callable(raw):
                raw = raw()
            return raw
        except Exception:
            return None

    return {
        "x": _val("x"),
        "y": _val("y"),
        "width": _val("width"),
        "height": _val("height"),
        "maximized": bool(_val("maximized")),
    }


def run_native_window(url: str) -> None:
    require_webview2()
    webview = _load_webview()
    geometry = load_geometry()
    kwargs = window_kwargs(url=url, geometry=geometry)
    try:
        window = webview.create_window(**kwargs)
    except Exception as exc:
        raise DesktopError("webview2_unavailable", WEBVIEW2_ERROR) from exc

    def on_closed() -> None:
        try:
            save_geometry(_geometry_from_window(window))
        except Exception as exc:
            log_desktop("warn", f"geometry persist: {exc}")

    window.events.closed += on_closed

    def on_shown() -> None:
        try:
            _assert_edgechromium(webview.renderer)
        except DesktopError:
            log_desktop("error", "renderer was not edgechromium; closing")
            window.destroy()

    window.events.shown += on_shown
    try:
        webview.start(**WEBVIEW_START_KWARGS)
    except DesktopError:
        raise
    except Exception as exc:
        raise DesktopError("webview2_unavailable", WEBVIEW2_ERROR) from exc
    _assert_edgechromium(webview.renderer)


def run_hidden_webview_smoke(url: str, timeout: float = 20.0) -> dict[str, Any]:
    require_webview2()
    webview = _load_webview()
    result: dict[str, Any] = {"renderer": None, "loaded": False}
    kwargs = window_kwargs(url=url)
    kwargs["hidden"] = True
    kwargs["width"] = MINIMAL_SMOKE_WIDTH
    kwargs["height"] = MINIMAL_SMOKE_HEIGHT
    window = webview.create_window(**kwargs)

    def on_loaded() -> None:
        result["renderer"] = webview.renderer
        result["loaded"] = True
        window.destroy()

    def watchdog() -> None:
        time.sleep(timeout)
        if not result["loaded"]:
            result["renderer"] = webview.renderer
            try:
                window.destroy()
            except Exception:
                pass

    window.events.loaded += on_loaded
    threading.Thread(target=watchdog, name="clankops-desktop-smoke-watch", daemon=True).start()
    try:
        webview.start(**WEBVIEW_START_KWARGS)
    except Exception as exc:
        raise DesktopError("webview2_unavailable", WEBVIEW2_ERROR) from exc
    return result
