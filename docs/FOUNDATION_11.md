# Foundation 11 — Mission lifecycle reconciliation and control-plane consolidation

Foundation 10 is merged. ClankOps can observe managed process exit without
pretending that exit was a handoff. It still could not tell the truth about
COPS-000011.

COPS-000011 is Foundation 5. Foundation 5 was delivered and merged:

- PR #6
- approved head `ae5669b6dc6370edf51cb47eb80e256151a6e803`
- merge commit `d1a6f3b886b5044ffd8e530a52d912e1fae973c2`

The Mission remained `PAUSED`. Ordinary `MISSION_TRANSITIONS` do not allow
`PAUSED -> COMPLETED`. Resuming it to `ACTIVE` merely to complete it would
fabricate an ACTIVE interval and possibly a Session for work that did not
resume. That is forbidden.

Foundation 11 adds an explicit, auditable **reconciliation** mechanism so
ClankOps can say:

> The ledger previously said PAUSED. We now have sufficient evidence that
> the objective had in fact completed. The operator reconciled the Mission
> to COMPLETED at time T.

without pretending ClankOps observed that completion live.

## Law

**Reconciliation corrects present knowledge. It does not rewrite history.**

Never:

- backdate the reconciliation event
- fabricate an ACTIVE interval, Session, handoff, checkpoint, or `next_action`
- rewrite or delete the original `PAUSED` / `BLOCKED` event
- pretend ClankOps observed completion when it did not
- silently alter Mission history
- auto-complete a Mission because a PR merged, CI succeeded, or a commit exists

Ordinary work still uses `MISSION_STATE_CHANGED` and `transition_mission()`.
Reconciliation is not a looser transition table.

## Ordinary transition vs reconciliation

| | Ordinary transition | Reconciliation |
| --- | --- | --- |
| Event | `MISSION_STATE_CHANGED` | `MISSION_STATE_RECONCILED` |
| When | ClankOps observed the lifecycle change | present projected state is stale vs demonstrable evidence |
| Session | leaving ACTIVE work ends open Sessions | never creates or ends a Session |
| Timestamp | when the transition was recorded | when reconciliation was recorded |
| `ACTIVE -> COMPLETED` | yes, keep using this for live cooperative completion | yes, historical correction only: zero open Sessions, explicit USER action, evidence-backed |

The distinction survives forever in the ledger.

## Allowed targets (this slice)

Unfinished Mission → `COMPLETED` only, when explicitly requested and
supported by evidence:

- `PLANNED -> COMPLETED`
- `PAUSED -> COMPLETED`
- `BLOCKED -> COMPLETED`
- `ACTIVE -> COMPLETED` when every Session on that Mission is already
  closed. This does not fabricate an ACTIVE interval: the Mission already
  projects ACTIVE. It does not create a Session, checkpoint, handoff, or
  `next_action`. Ordinary live `ACTIVE -> COMPLETED` remains
  `transition_mission` / `complete` / `handoff --state COMPLETED`.

Not added:

- `COMPLETED -> ACTIVE`
- `ABANDONED -> COMPLETED`
- `SUPERSEDED -> COMPLETED`

Terminal history is not casually rewritten.

## Event

`MISSION_STATE_RECONCILED`

Payload:

- `reconciliation_id`, `mission_id`, `clank_id`
- `from_state`, `to_state`, `reason`
- `evidence`: list of `{kind, value, source}`
- `evidence_occurred_at` (optional; timestamp from the external evidence)
- `reconciliation_basis`
- `observed_at`

`event.ts_utc` is always when reconciliation occurred (store clock).
`evidence_occurred_at` may be earlier. It must not replace the event timestamp.

Example:

```text
event.ts_utc:            2026-09-14...
evidence_occurred_at:    2026-09-11...
```

Meaning: we learned/reconciled this now. The supporting fact happened earlier.

`bind_session=False`. `session_id` is null. No `SESSION_STARTED`,
`SESSION_ENDED`, `CHECKPOINT_RECORDED`, or fake handoff.

## Provenance

Reconciliation provenance distinguishes:

1. who performed / authorised the reconciliation (the ClankOps actor)
2. what evidence supports it
3. where that evidence came from

GitHub is evidence. The reconciliation action is an explicit ClankOps
action. Do not mark the event `source=GITHUB` as if GitHub autonomously
changed the Mission.

For COPS-000011:

- reconciliation actor: operator / `user`
- event source: `USER`
- evidence source: `GITHUB`
- evidence: PR #6, merge SHA `d1a6f3b886b5044ffd8e530a52d912e1fae973c2`

`reconciliation_basis` is required (no silent inference):
`operator-confirmed`, `github`, `ci`, `deployment`, `reconstructed`,
`local_git`, `artefact`.

## CLI

```text
clankctl mission reconcile COPS-000011 \
    --to COMPLETED \
    --reason "Foundation 5 was merged but Mission remained PAUSED" \
    --evidence github:pr:6 \
    --evidence github:commit:d1a6f3b886b5044ffd8e530a52d912e1fae973c2 \
    --basis github \
    --evidence-occurred-at 2026-09-11T...
```

Requirements:

- explicit Mission
- explicit `--to`
- explicit `--reason`
- at least one `--evidence`
- explicit `--basis`
- event `--source` must be `USER` (evidence planes are rejected before write)
- zero silent inference
- no "pick latest unfinished Mission"
- no automatic GitHub fetch as part of the mutation
- no Session required, created, or resumed
- no checkpoint created

This is not top-level `clankctl reconcile` (Foundation 3 git/GitHub
observation). That command stays read-only observation.

Evidence items are structured, not an opaque prose blob:

| Token | kind | source |
| --- | --- | --- |
| `github:pr:N` | `github_pr` | GITHUB |
| `github:commit:SHA` | `github_merge_sha` | GITHUB |
| `github:url:URL` | `github_url` | GITHUB |
| `ci:run:ID` | `ci_run` | CI |
| `artefact:ID` | `artefact_id` | SYSTEM |

A later Foundation may automate evidence validation. This slice trusts
the operator to supply it.

## Projection and timeline

After reconciliation, the Mission projection is `state = COMPLETED`.
History still shows the previous state and the later reconciliation.

Timeline rendering must not look like a natural `PAUSED -> COMPLETED`
transition:

```text
[PAUSED] ...
[RECONCILED -> COMPLETED]
  reason: Foundation 5 merged while ledger remained paused
  evidence: PR #6 / d1a6f3b...
  reconciled: 2026-09-14...
  evidence occurred: 2026-09-11...
```

A completed reconciled Mission is no longer unfinished. It must not keep
ClankOps permanently `AMBIGUOUS`.

A real reconciliation event changes `context_fingerprint`. Display-only
ages must not. Foundation 8/10 fingerprint laws are unchanged.

`rebuild` replays `MISSION_STATE_RECONCILED` into `mission_reconciliations`
and updates `missions.state`. Original events remain append-only.

## Session close vs handoff

Foundation 10 exposed a closed Session as `handoff: RECORDED`. A plain
Session end and a Foundation 1 handoff are not the same thing.

Derived state:

| Session | Handoff |
| --- | --- |
| `OPEN` | `MISSING` |
| `CLOSED` + `HANDOFF_RECORDED` for that Session | `RECORDED` |
| `CLOSED` without that event | `UNKNOWN` |

`HANDOFF_RECORDED` is emitted **only** by canonical `handoff_mission()`.

Do **not** infer handoff from:

- `CHECKPOINT_RECORDED` plus `SESSION_ENDED reason=mission_*`
- ordinary `mission pause` / `block` / `complete`
- `session end`

A checkpoint followed by a direct pause produces `CLOSED` / `UNKNOWN`.
Historical Sessions without the explicit event remain `UNKNOWN`.

**UNKNOWN stays UNKNOWN.** Do not infer `HANDOFF RECORDED` merely because
`ended_utc` is non-null.

Process exit is still not a handoff. Process exit is still not Session close.

## Reconciliation source authority

The reconciliation **action** is a ClankOps operator mutation. Its event
`source` must be `USER`.

GitHub, CI, deployment, and local git are **evidence planes**. They may
appear on:

- `evidence[].source`
- `provenance.evidence_sources`
- `reconciliation_basis`

They must not be the event source.

```text
clankctl --source GITHUB mission reconcile ...
```

fails before writing. No `MISSION_STATE_RECONCILED` event is minted.

`--actor user` without `--source` defaults to `USER` and is accepted.

## Atomic reconciliation

Reconciliation is one reserved write:

1. `BEGIN IMMEDIATE`
2. re-read the Mission under the lock
3. validate current state (`PLANNED` / `PAUSED` / `BLOCKED` / `ACTIVE`)
4. refuse if any Session on that Mission is still open
5. append `MISSION_STATE_RECONCILED` with `from_state` equal to the
   locked observation
6. project
7. commit

Any failure rolls back. Never reconcile around a live Session. Ordinary
`transition_mission` / `resume_mission` take the same reserved write lock
and re-read Mission state under it, so a concurrent resume cannot overwrite
a just-reconciled `COMPLETED` or leave `COMPLETED` with an open Session.

## Attention

`STALE_MISSION_STATE_EVIDENCE_CONFLICT` is **out of scope**. Age is not
completion evidence. Automatic contradiction detection would guess.
Reconciliation is mandatory; auto-detection is not.

Foundation 7 Attention remains derived and writes zero events.

## COPS-000011 acceptance case

After implementation and tests are green, reconcile the real Mission
without resuming it:

```text
python -m clankops --actor user --source USER mission reconcile COPS-000011 \
    --to COMPLETED \
    --reason "Foundation 5 implementation was reviewed and merged, but the ClankOps Mission remained PAUSED." \
    --evidence github:pr:6 \
    --evidence github:commit:d1a6f3b886b5044ffd8e530a52d912e1fae973c2 \
    --basis github
```

Do not fabricate a resumed Session. Do not create an ACTIVE transition
first. Do not backdate the event. COPS-000018 remains the Foundation 11
Mission.

## Control-plane audit (Foundations 0–10)

Foundation 11 is the first deliberate cross-Foundation audit. One plane
must not be silently promoted into another.

### Zero-conflation laws

1. Git commit exists ≠ Mission completed.
2. PR merged ≠ Mission completed unless an explicit reconciliation or
   ordinary transition records that conclusion.
3. CI success ≠ Mission completed.
4. Process exit ≠ Session closed.
5. Session closed ≠ handoff recorded.
6. Deployment running ≠ authoritative.
7. Old evidence ≠ false evidence.
8. Missing evidence ≠ negative evidence.
9. Reconciliation ≠ historical observation.
10. Reconciliation timestamp ≠ evidence timestamp.
11. Actor / launcher identity ≠ permission.
12. Attention ≠ authoritative Mission state.

ClankOps does not steal authority from Standards Clank, Motherclank, or
Quartermaster.

## Out of scope

- Foundation 12
- Generic workflow engine
- Auto-completing Missions from PR merges
- Auto-closing Sessions / auto-handoffs
- `STALE_MISSION_STATE_EVIDENCE_CONFLICT`
- Process control
- Standards / Motherclank / Quartermaster authority
- LLM-generated reconciliation
- Vector search
- Dependency-graph UI
- Massive Terminal redesign
