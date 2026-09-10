# Cursor agent contract (Foundation 1)

Shared instruction fragment. Do not fork copies into every Clank repo; point here.

Canonical commands assume the ClankOps tree is on `PYTHONPATH` or installed editable:

`python -m clankops`  (same as `clankctl` when Scripts is on PATH)

## Law

1. Read `python -m clankops brief <clank>` before modifying that Clank.
2. Work only under the assigned Mission and Session. A Mission is an objective, not an IDE window.
3. Record meaningful checkpoints, not tool-call spam.
4. Record concrete tasks, decisions, features, and blockers as they arise.
5. Capture Git evidence after implementation milestones (`--capture-git` or `handoff`).
6. Never claim local Git evidence is GitHub evidence. Use `LOCAL_GIT` vs `GITHUB`.
7. Before stopping, leave an exact handoff checkpoint and pause/block/complete/abandon the Mission.
8. Never log secrets.
9. Never invent historical state. Unknown next actions stay unknown.
10. Never abandon an ACTIVE Session by simply ending a reply when a handoff can be written.

Crashes and killed processes are not policy violations. Ignoring a working logger is.

## Start / resume

```text
python -m clankops --actor cursor brief oem-radar
python -m clankops --actor cursor work resume oem-radar
# or, only for a new objective:
python -m clankops --actor cursor work start oem-radar "<objective>"
```

`work resume` never invents a Mission. If one unfinished Mission exists, it is resumed. If several exist, pass the Mission id. Export the printed `$env:CLANKOPS_*` values (or run `scripts/clankops-dev.ps1 resume oem-radar`).

## Stop / handoff

```text
python -m clankops --actor cursor handoff COPS-000003 --state PAUSED --completed "..." --current "..." --next "..." --tests "python -m pytest: N passed"
```

One command checkpoints (with local git capture), then sets Mission state. Sessions close when the Mission leaves ACTIVE.

## Environment

| Variable | Meaning |
| --- | --- |
| `CLANKOPS_DB` | Ledger path (default `%USERPROFILE%\.clankops\clankops.db`) |
| `CLANKOPS_ACTOR` | Actor string (`cursor`, `codex`, …) |
| `CLANKOPS_CLANK_ID` | Durable Clank UUID |
| `CLANKOPS_CLANK_SLUG` | Human slug |
| `CLANKOPS_MISSION_ID` | Durable Mission UUID |
| `CLANKOPS_MISSION_DISPLAY` | `COPS-000123` |
| `CLANKOPS_SESSION_ID` | Open Session UUID |
| `CLANKOPS_CONTEXT_FILE` | This Clank's `contexts\<clank-id>.json` |
| `CLANKOPS_CLANK_PATH` | Canonical local checkout for LaunchCursor / identity |
| `CLANKOPS_HOME` | Override directory for context files (tests). Not the git checkout. |
| `CLANKOPS_ROOT` | Git checkout of clankops (PowerShell launcher / `PYTHONPATH`) |

`python -m clankops work env` validates the Session for **this** Clank/workspace. A last-active pointer from another Clank is not enough. Invalid, closed, or cross-Clank Sessions fail with `CLANKOPS INTEGRATION FAILED` and a recovery command. Emergency coding is not blocked; do not keep a bad `CLANKOPS_SESSION_ID`.

Cursor `--actor cursor` records semantic checkpoints as `AGENT_REPORT` unless `--source` is set explicitly. `--capture-git` / `handoff` attach `payload.git_evidence` with `source=LOCAL_GIT`; they do not relabel completed/current/next/notes as local git, and they never mint `GITHUB` facts.

Dashboard/collection `.cmd` files under `_Launchers` are not Cursor launchers. Do not wrap them with Sessions. Collection stays a manual GUI action.
