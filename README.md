# ClankOps

Development control plane and institutional memory for the Clank ecosystem.

Foundation 1 is live-fleet adoption on top of the Foundation 0.1 ledger. Current state is a projection of an append-only event history. The goal is to make it impossible to lose the state, rationale, outstanding work, or next action of any Clank.

```text
clankctl brief oem-radar
```

should answer: what this Clank is, which mission is unfinished, where development stopped, what remains, and — only if someone recorded it — what to do next.

## Status

Foundation 1 (adoption). Local-first Python 3.14+ + SQLite. No UI. No production deployment.

ClankOps does **not** replace Motherclank (fleet laws), Standards Clank (conformance), Quartermaster (model/resource/quota), or Unified Clank Platform (shared runtime). It observes development state.

## Install

Python 3.14+. From this repository:

```powershell
python -m pip install -e ".[dev]"
```

`clankctl` is the CLI. If Scripts is not on PATH, invoke as:

```powershell
python -m clankops.cli --help
```

Default database: `%USERPROFILE%\.clankops\clankops.db` (override with `--db` or `CLANKOPS_DB`).

## Quick start

```powershell
clankctl init
clankctl census --out data\bootstrap\clank_census.json --import
clankctl list
clankctl brief oem-radar
```

Log work on a Clank:

```powershell
python -m clankops --actor cursor brief oem-radar
python -m clankops --actor cursor work resume oem-radar
python -m clankops --actor cursor handoff COPS-000003 --state PAUSED --current "..." --next "..." --capture-git
```

`work resume` reuses an unfinished Mission. `handoff` is checkpoint + git capture + Mission state in one step. See [Foundation 1](docs/FOUNDATION_1.md).

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Event model](docs/EVENT_MODEL.md)
- [Census](docs/CLANK_CENSUS.md)
- [Agent logging contract](docs/AGENT_LOGGING_CONTRACT.md)
- [Cursor agent contract](docs/CURSOR_AGENT_CONTRACT.md)
- [Foundation 1 adoption](docs/FOUNDATION_1.md)
- [Future scope](docs/FUTURE_SCOPE.md)

## Tests

```powershell
python -m pytest
```

## Identity

Durable IDs are UUIDv7. Missions also get a human display id (`COPS-000123`). Filesystem paths and GitHub names are references, not identity.
