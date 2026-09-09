# Architecture — Foundation 0

## Law

Current state is a projection of immutable history.

ClankOps is not a CRUD app. Mutations append events. Corrections append compensating events. Projection tables exist only for query convenience and must be rebuildable from `events`.

## Why this system exists

Development jumps between Clanks. Work is interrupted, abandoned, forgotten, or resumed months later. ClankOps stores identity, missions, checkpoints, tasks, features, decisions, blockers, and artefacts so a future agent can resume without the original chat transcript.

## Authority boundaries

| System | Owns |
| --- | --- |
| Motherclank | Fleet laws / foundational doctrine |
| Standards Clank | Conformance |
| Quartermaster | Model / resource / quota decisions |
| Unified Clank Platform | Shared runtime architecture |
| Individual Clanks | Their domain |
| ClankOps | Development state, history, provenance, mission tracking, cross-project visibility |

ClankOps must not absorb the others. Integration is future work; see [FUTURE_SCOPE.md](FUTURE_SCOPE.md).

## Stack

Boring on purpose:

- Python 3.13+
- SQLite in WAL mode
- SQL migrations in `clankops.schema`
- Typed-enough domain layer (`clankops.store`)
- CLI `clankctl`

Not in Foundation 0: Kubernetes, Kafka, Redis, Elasticsearch, microservices, cloud databases, dashboards, webhooks, LLM summaries.

SQLite must not live on a network share with concurrent remote writers. Future remote writes go through a service. Foundation 0 is local-first. Default path: `%USERPROFILE%\.clankops\clankops.db`.

## Layers

```text
clankctl  →  Store  →  events (append-only) + projections
                 ↘ census scanner (read-only filesystem / git / gh)
```

1. **Census** discovers candidates. It never mutates other repositories. Dirty trees are evidence.
2. **Store** appends an event, then applies it to projection tables in the same transaction.
3. **Rebuild** deletes projection rows and replays events ordered by `(ts_utc, event_id)`.

## Identifiers

- **Primary keys:** UUIDv7 (time-ordered, stable across rename and move).
- **Mission display ids:** `COPS-000123` from an `id_sequences` table. Display ids are labels, not keys.
- Paths, remotes, and GitHub repo names are stored on `clank_refs` and may change.

## Provenance

Every event has `source`:

| Source | Meaning |
| --- | --- |
| USER | Human typed it |
| AGENT_REPORT | An agent claimed it |
| LOCAL_GIT | Observed from local git (read-only) |
| GITHUB | Observed from GitHub |
| CI | Observed from CI |
| DEPLOYMENT | Observed from a deployment system |
| SYSTEM | ClankOps itself |
| RECONSTRUCTED | Bootstrap / census / inferred history |

Agent statements are not automatically GitHub facts. If an agent reports a push and GitHub cannot corroborate it, the ledger keeps an `AGENT_REPORT` event. It does not rewrite the event as `GITHUB`.

Reconstructed census facts must remain `RECONSTRUCTED`. Unknown timestamps stay unknown; event `ts_utc` is when ClankOps recorded the fact, not a fabricated historical time.

## Storage invariants

- `events` has SQL triggers forbidding UPDATE and DELETE.
- WAL + `foreign_keys=ON` + `busy_timeout`.
- Projection rebuild is an acceptance test: a fresh database containing only the event rows must reconstruct identical projection state.

## Domain objects

See [EVENT_MODEL.md](EVENT_MODEL.md) for fields and event types. Summary:

- **Clank** — permanent project identity
- **Mission** — coherent development objective that survives handoff
- **Session** — one actor working a Mission
- **Event** — immutable ledger record
- **Feature / Task / Decision / Blocker / Artifact / Relationship**

Relationship kinds are free strings with a recommended vocabulary. Adding a kind does not require schema surgery.

## Census vs live history

`data/bootstrap/clank_census.json` is a reconstruction artefact. Importing it creates `CENSUS_CANDIDATE_RECORDED` events (and Clank registrations for registerable classifications) with `source=RECONSTRUCTED`. It is not equivalent to live ClankOps history.
