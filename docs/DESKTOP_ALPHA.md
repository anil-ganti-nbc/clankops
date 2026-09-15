# Desktop Alpha — native Windows shell

Desktop Alpha puts a native window around Terminal Beta. It does not
replace Terminal, duplicate its read model, or acquire ledger
authority.

```text
DESKTOP PRESENTS TERMINAL.
DESKTOP DOES NOT DUPLICATE TERMINAL.

TERMINAL PRESENTS EVIDENCE.
TERMINAL DOES NOT CREATE AUTHORITY.
```

## Architecture

```text
ClankOps.exe
    |
    +-- acquire Windows named mutex Local\ClankOpsDesktop
    |
    +-- resolve ClankOps DB (--db > CLANKOPS_DB > %USERPROFILE%\.clankops\clankops.db)
    |
    +-- validate DB read-only (no create, no migrate)
    |
    +-- clankops.terminal.serve(host="127.0.0.1", port=0, allow_remote=False)
    |
    +-- background serve_forever()
    |
    +-- pywebview 6.2.1 / Edge Chromium WebView2
            http://127.0.0.1:<ephemeral>/
    |
    +-- close window
            shutdown -> server_close -> join thread
            -> release mutex -> process exits
```

There is no second HTTP implementation, no Python/JS bridge, and no
subprocess for Terminal.

## WebView2 requirement

Desktop Alpha requires the **system-installed Microsoft Edge WebView2
Runtime**. It does not bundle Evergreen runtime and does not fall back
to deprecated MSHTML / Internet Explorer.

If WebView2 cannot initialise, Desktop exits with a visible error. The
CLI and `clankctl terminal` remain usable.

Operator machine at Alpha acceptance: WebView2 Runtime **152.0.4191.66**
was present; a bounded spike loaded a loopback page with
`renderer = edgechromium`.

## pywebview role

`pywebview==6.2.1` is an **optional extra**. It is the native window
host (WinForms + Edge Chromium). Core ClankOps still has zero runtime
dependencies. `clankctl` does not import it.

```powershell
python -m pip install -e ".[desktop]"
python -m clankops.desktop
```

Conservative host settings:

- Edge Chromium / WebView2 only (`gui=edgechromium`)
- downloads disabled
- `file://` access disabled
- remote debugging disabled
- DevTools disabled in normal builds
- private browser mode
- external `target=_blank` links may open in the system browser
- no `js_api` / `window.pywebview.api`

## Embedded Terminal lifecycle

Desktop calls the existing `serve()` helper with loopback and port `0`.
The assigned port is read from `server.server_address`. Port **8765 is
not** the Desktop identity; a manually running Terminal can keep that
port.

On window close, Desktop:

1. `server.shutdown()`
2. `server.server_close()`
3. joins the server thread with a bounded timeout
4. releases the single-instance mutex
5. exits the process

If startup fails after the socket binds, the server is cleaned up
before return.

## Single-instance law

Windows uses a named mutex `Local\ClankOpsDesktop`. A second launch
does not start another Terminal server or open another DB. It exits
with "ClankOps is already running." The kernel releases the mutex if
the process crashes. Bringing the first window to the foreground is
not required in Alpha.

## Database resolution

1. `--db`
2. `CLANKOPS_DB`
3. `%USERPROFILE%\.clankops\clankops.db`

Desktop is read-only. It will not create a missing ledger, will not
run migrations, and will not open the write-capable Store merely to
validate startup. Missing, unreadable, or incompatible schema fails
with a visible error and a non-zero exit.

Opening the client writes zero ledger events.

## Default mode

The first URL is `/` with no query flags. That is Terminal **SNAPSHOT**
mode. Desktop does not start `?live=1` or `?github=1`. Default launch
runs zero Git commands, zero GitHub calls, zero Harvest invocations,
and zero Task Scheduler mutations.

There are no mutation buttons.

## Window

- title: `ClankOps`
- about 1440×900, minimum about 1000×650
- resizable ordinary Windows frame
- not always-on-top
- no close confirmation
- no custom titlebar in Alpha

Presentation geometry may be saved to
`%LOCALAPPDATA%\ClankOps\desktop.json` (`x`, `y`, `width`, `height`,
`maximised`). That file is not ledger evidence. A corrupt file is
ignored.

## Failure behaviour

Because the packaged EXE is windowed, startup failures use a native
error dialog plus stderr when a console exists:

- missing / unreadable / incompatible DB
- WebView2 unavailable
- server bind failure
- already running
- unexpected exception (user-facing text; traceback only in the log)

Optional diagnostic log: `%LOCALAPPDATA%\ClankOps\logs\desktop.log`
(size-bounded). It records shell lifecycle/errors only. It is not
ledger evidence. Tokens, webhook URLs, Git credentials, and environment
dumps are not written.

## Smoke mode

Not a public control API:

```powershell
python -m clankops.desktop --db <ledger> --smoke-test out.json
ClankOps.exe --db <ledger> --smoke-test out.json
ClankOps.exe --db <ledger> --smoke-test out.json --webview-smoke
```

HTTP smoke starts the embedded Terminal on ephemeral loopback, `GET /`,
checks HTTP 200 and `X-ClankOps-Mode=read-only`, shuts down, writes
bounded JSON, and exits without ledger writes.

`--webview-smoke` additionally opens a hidden WebView2 window, asserts
`renderer = edgechromium`, closes, and exits.

## Packaging

Alpha target is **ONEDIR**, not onefile, no installer:

```text
dist\ClankOps\ClankOps.exe
dist\ClankOps\_internal\
```

Windowed (no console on double-click). Developer path:

```powershell
python -m clankops.desktop
```

Build (Windows, Python ≥ 3.14, desktop extra, PyInstaller 6.22.x):

```powershell
python -m pip install -e ".[desktop,desktop-build]"
.\scripts\build-desktop.ps1
```

The script verifies Windows/Python/pywebview/PyInstaller, deletes only
`dist\ClankOps` and `build\ClankOps`, and prints EXE path, size, and
SHA-256. Spec: `packaging/ClankOps.spec`.

Do not commit `dist/`, `build/`, packaged EXEs, or generated pythonnet
binaries. The EXE is a local acceptance artefact.

No MSI, MSIX, or auto-updater in Alpha. No bundled WebView2 runtime.
No custom icon in Alpha unless one already exists (none did).

## Relationship to Fleet Pulse

Independent. Desktop does not install, start, stop, or configure Pulse.
If Pulse later writes fresher harvest evidence into the ledger, Desktop
shows it because Terminal reads the DB. Production task
`ClankOps Fleet Harvest` is not created by Desktop.

## Limitations (Alpha)

- Windows only
- system WebView2 Runtime required
- no installer / Start Menu / tray / run-at-login
- no mutations, Harvest-now, Pulse UI, or agent launch buttons
- no macOS or Linux package
- Terminal look-and-feel is unchanged

Those belong to Desktop Beta or later, documented in
[Future scope](FUTURE_SCOPE.md).

## Alpha measurements (operator machine)

Not a hard gate. Recorded so Desktop Beta has a baseline.

- WebView2 Runtime: 152.0.4191.66 (system, not bundled)
- Renderer: `edgechromium` (no MSHTML fallback)
- Cold launch to visible window: about 2.0 s (`python -m clankops.desktop`)
- Warm launch to visible window: about 1.0 s
- Packaged EXE visible: about 0.8 s
- Idle: ~102 MB working set, ~0 CPU over 5 s after startup
- ONEDIR `ClankOps.exe` size (this machine's build): about 8.2 MB plus `_internal`
- Production Task Scheduler task `ClankOps Fleet Harvest`: absent during Alpha
