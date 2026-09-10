# ClankOps

Development control plane and institutional memory for the Clank ecosystem.

Foundation 4 observes GitHub commit check-runs as additional evidence. Empty checks are `none`, never success. Git corroboration from Foundation 3 is unchanged.

```text
clankctl brief oem-radar
```

should answer: what this Clank is, which mission is unfinished, where development stopped, what remains, and — only if someone recorded it — what to do next.

## Status

Foundation 4 (GitHub commit-status / CI evidence). Local-first Python 3.14+ + SQLite. Read-only localhost Terminal. No production deployment. No cron.

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

Adopt remaining VERIFIED census identities (duplicate checkouts become extra refs, not second Clanks):

```powershell
python -m clankops --actor cursor fleet-adopt-verified --file data\bootstrap\clank_census.json
python -m clankops --json coverage
python -m clankops --json sessions open
python -m clankops --json sessions stale --older-than 24h
python -m clankops --json reconcile clankops
python -m clankops terminal
```

The Terminal is localhost-only and read-only. It does not schedule collection or deploy anything.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Event model](docs/EVENT_MODEL.md)
- [Census](docs/CLANK_CENSUS.md)
- [Agent logging contract](docs/AGENT_LOGGING_CONTRACT.md)
- [Cursor agent contract](docs/CURSOR_AGENT_CONTRACT.md)
- [Foundation 1 adoption](docs/FOUNDATION_1.md)
- [Foundation 2 fleet + Terminal](docs/FOUNDATION_2.md)
- [Foundation 3 git/GitHub reconciliation](docs/FOUNDATION_3.md)
- [Foundation 4 GitHub CI evidence](docs/FOUNDATION_4.md)
- [Future scope](docs/FUTURE_SCOPE.md)

## Tests

```powershell
python -m pytest
```

## Identity

Durable IDs are UUIDv7. Missions also get a human display id (`COPS-000123`). Filesystem paths and GitHub names are references, not identity.
