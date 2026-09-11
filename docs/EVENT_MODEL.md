# Event model — Foundation 0

Storage timestamps are UTC (`YYYY-MM-DDTHH:MM:SS.ffffffZ`). Display may use Asia/Kolkata; the ledger does not.

## Event record

| Field | Notes |
| --- | --- |
| `ledger_seq` | Canonical order in this ledger. Assigned at append time, unique, monotonic, immutable. |
| `event_id` | UUIDv7 durable global identity. Not replaced by `ledger_seq`. |
| `ts_utc` | When ClankOps recorded the event (evidence, not the ordering primitive) |
| `clank_id` | Durable Clank id when applicable |
| `mission_id` | Durable Mission id when applicable |
| `session_id` | Durable Session id when applicable |
| `event_type` | Closed set below |
| `actor` | Free string (user, cursor, grok, …) — not vendor-locked |
| `source` | USER / AGENT_REPORT / LOCAL_GIT / GITHUB / CI / DEPLOYMENT / SYSTEM / RECONSTRUCTED |
| `payload_json` | Structured body |
| `provenance_json` | Recorder metadata; reconstructed flag when relevant |
| `schema_version` | Payload/schema generation |

Corrections are new events. Existing events are never silently edited to represent new truth.

## Event types

| Type | Payload (minimum) |
| --- | --- |
| `CLANK_REGISTERED` | slug, display_name, aliases, refs, lifecycle, classification |
| `CLANK_ALIAS_ADDED` | alias |
| `CLANK_REF_UPDATED` | kind, value, canonical, replace_kind |
| `CLANK_LIFECYCLE_CHANGED` | lifecycle, from_lifecycle |
| `MISSION_CREATED` | display_id, objective, state |
| `MISSION_STATE_CHANGED` | from_state, to_state, optional reason / superseded_by |
| `SESSION_STARTED` | actor |
| `SESSION_ENDED` | — |
| `CHECKPOINT_RECORDED` | completed, current_work, next_action, outstanding, blockers, tests, optional branch/head/working_tree, optional git_evidence, artifacts, notes |
| `TASK_CREATED` | task_id, title, state |
| `TASK_STATE_CHANGED` | task_id, from_state, to_state |
| `FEATURE_ADDED` | feature_id, name, state |
| `FEATURE_STATE_CHANGED` | feature_id, from_state, to_state |
| `DECISION_RECORDED` | decision_id, statement, why, alternatives |
| `DECISION_SUPERSEDED` | decision_id, superseded_by |
| `BLOCKER_ADDED` | blocker_id, description |
| `BLOCKER_RESOLVED` | blocker_id, resolution |
| `ARTIFACT_ATTACHED` | artifact_id, kind, ref, artifact_source, metadata |
| `RELATIONSHIP_RECORDED` | relationship_id, from_clank_id, to_clank_id, kind |
| `CENSUS_CANDIDATE_RECORDED` | full candidate snapshot |
| `DEPLOYMENT_OBSERVED` | observation_id, surface_id, environment, host_identity, runtime_path, deployed_sha, image_id, runtime_identity, deployed, running, scheduler, scheduler_cadence, state_store, collection_authority, notification_authority, webhook_configured, sent_count, observed_at, observed_how, observer, notes, sanitised metadata |

## Mission states

`PLANNED` → `ACTIVE` / `PAUSED` / `BLOCKED` / `ABANDONED` / `SUPERSEDED`

`ACTIVE` → `PAUSED` / `BLOCKED` / `COMPLETED` / `ABANDONED` / `SUPERSEDED`

`PAUSED` → `ACTIVE` / `BLOCKED` / `ABANDONED` / `SUPERSEDED`

`BLOCKED` → `ACTIVE` / `PAUSED` / `ABANDONED` / `SUPERSEDED`

`COMPLETED` / `ABANDONED` → `SUPERSEDED` only

`SUPERSEDED` is terminal.

Invalid transitions raise; they do not write events.

## Feature states

`PROPOSED`, `PLANNED`, `IN_PROGRESS`, `PRESENT`, `DEPRECATED`, `REMOVED`, `REJECTED`

## Task states

`TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`, `CANCELLED`

Done and cancelled are terminal.

## Checkpoint

A checkpoint is how ClankOps answers “where exactly did we leave this?”. Fields are optional; unknown stays unknown. `next_action` is only shown if recorded. Foundation 0 never asks an LLM to invent one.

Event `source` describes who made the semantic claims (typically `AGENT_REPORT` for Cursor, `USER` when a human says so, `RECONSTRUCTED` for inferred history). `--capture-git` and `handoff` attach inspected branch / HEAD / dirty-or-clean under `payload.git_evidence` with `source=LOCAL_GIT`. Capturing git does not rewrite the checkpoint event's source, does not turn completed/current/next/notes into git facts, and is not a GitHub confirmation.

## Artifacts

Artifacts *reference* external systems (commit SHA, PR URL, test run id). They do not duplicate those databases. `artifact_source` must reflect how the reference was obtained (`AGENT_REPORT` vs `LOCAL_GIT` vs `GITHUB` vs `CI`).

## Relationships

Recommended kinds: `DEPENDS_ON`, `PROVIDES_TO`, `SUPERSEDES`, `REPLACED_BY`, `SHARES_RUNTIME_WITH`, `GOVERNED_BY`, `DEPLOYED_WITH`, `DUPLICATE_CHECKOUT_OF`, `LOCAL_COPY_OF`. The column is a string; new kinds do not require a migration.

## Event identity vs ledger order

- `event_id` is the globally unique durable identity of an event (UUIDv7). It survives export, copy, and future multi-writer APIs.
- `ledger_seq` is the canonical order **within this SQLite ledger**. Replay, history, and projections use `ORDER BY ledger_seq`. Two events may share `ts_utc`; their order is still deterministic.

Existing Foundation 0 rows are migrated by assigning `ledger_seq` in the old `(ts_utc, event_id)` order, then freezing that order.

## Session lifecycle

A Session is one actor's continuous period of active work on one Mission.

- Starting or resuming a Mission (to `ACTIVE`) opens a Session.
- `clankctl session start <mission>` opens another Session for a different actor. A Mission may have multiple open Sessions.
- A Session ends on explicit `session end`, or when the Mission leaves active work (`PAUSED`, `BLOCKED`, `COMPLETED`, `ABANDONED`, `SUPERSEDED`). All open Sessions on that Mission close. Historical Sessions remain.
- Duration is `ended_utc - started_utc` when both are known.
- Mutations during a Session (checkpoints, tasks, features, decisions, blockers, artefacts) carry `session_id` when it can be resolved. An attributed Session must exist, still be open, and match the event's Mission (when present), Clank (when present), and actor. An explicit `--session` or `CLANKOPS_SESSION_ID` that fails those checks is a validation error; ClankOps does not drop it and write an unattributed event. If none is supplied and exactly one open Session exists for this actor on this Mission, that Session is bound. Otherwise attribution stays unknown. Reconstructed events may have none. System lifecycle operations (for example closing another actor's Session when a Mission leaves ACTIVE) use an explicit `bind_session=False` path rather than weakening normal validation.

## Projections

Tables `clanks`, `missions`, `sessions`, `features`, `tasks`, `decisions`, `blockers`, `artifacts`, `relationships`, `checkpoints`, `census_candidates`, plus alias/ref tables, are derived. `clankctl rebuild` wipes and replays. Tests assert byte-for-byte deterministic rebuilt state.
