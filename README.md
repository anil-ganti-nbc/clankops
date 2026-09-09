# ClankOps

Development control plane and institutional memory for the Clank ecosystem.

Foundation 0 is the ledger, not the dashboard. Current state is a projection of an append-only event history. The goal is to make it impossible to lose the state, rationale, outstanding work, or next action of any Clank.

```text
clankctl brief oem-radar
```

should answer: what this Clank is, which mission is unfinished, where development stopped, what remains, and — only if someone recorded it — what to do next.

## Status

Foundation 0. Local-first Python + SQLite. No UI. No production deployment.

ClankOps does **not** replace Motherclank (fleet laws), Standards Clank (conformance), Quartermaster (model/resource/quota), or Unified Clank Platform (shared runtime). It observes development state.

## Install

Python 3.13+. From this repository:

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
clankctl register watch-clank --path C:\Users\anil\Clanks\watch-clank --alias horology
clankctl mission start watch-clank "Fix specialist lead terminal state"
clankctl checkpoint COPS-000001 --current "investigating soak reviews" --next "re-run soak with labelled windows" --capture-git
clankctl task add COPS-000001 "update GGW window docs"
clankctl decision add COPS-000001 "Keep soak labels preliminary" --why "operator has not ratified promotion"
clankctl blocker add COPS-000001 "awaiting soak evidence"
clankctl mission pause COPS-000001
clankctl brief watch-clank
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Event model](docs/EVENT_MODEL.md)
- [Census](docs/CLANK_CENSUS.md)
- [Agent logging contract](docs/AGENT_LOGGING_CONTRACT.md)
- [Future scope](docs/FUTURE_SCOPE.md)

## Tests

```powershell
python -m pytest
```

## Identity

Durable IDs are UUIDv7. Missions also get a human display id (`COPS-000123`). Filesystem paths and GitHub names are references, not identity.
