"""ClankOps Desktop Alpha: native Windows shell around Terminal Beta.

DESKTOP PRESENTS TERMINAL. DESKTOP DOES NOT DUPLICATE TERMINAL.
The shell does not acquire ledger authority. Importing this package
does not import pywebview.
"""

from clankops.desktop.core import (
    DESKTOP_MUTEX_NAME,
    INITIAL_PATH,
    MIN_HEIGHT,
    MIN_WIDTH,
    WEBVIEW_GUI,
    WEBVIEW_SETTINGS,
    WEBVIEW_START_KWARGS,
    WINDOW_HEIGHT,
    WINDOW_TITLE,
    WINDOW_WIDTH,
    DesktopError,
    EmbeddedTerminal,
    SingleInstance,
    default_db_path,
    initial_url,
    main,
    resolve_db_path,
    run_desktop,
    run_smoke_test,
    validate_readonly_ledger,
    window_kwargs,
)

__all__ = [
    "DESKTOP_MUTEX_NAME",
    "INITIAL_PATH",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "WEBVIEW_GUI",
    "WEBVIEW_SETTINGS",
    "WEBVIEW_START_KWARGS",
    "WINDOW_HEIGHT",
    "WINDOW_TITLE",
    "WINDOW_WIDTH",
    "DesktopError",
    "EmbeddedTerminal",
    "SingleInstance",
    "default_db_path",
    "initial_url",
    "main",
    "resolve_db_path",
    "run_desktop",
    "run_smoke_test",
    "validate_readonly_ledger",
    "window_kwargs",
]
