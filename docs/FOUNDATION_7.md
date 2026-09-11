# Foundation 7 — Attention queue and evidence freshness

ClankOps should answer: **what am I in danger of forgetting right now, and why?**

This slice is a derived, read-only control-plane view. It does not mutate
Mission state, auto-close Sessions, schedule product Clanks, SSH, or mint a
unified Clank health score.

## Law

Freshness is evidence metadata, not truth.

- An old observation is not automatically bad.
- A deployed SHA differing from source HEAD is not automatically bad.
- A paused Mission is not automatically bad.
- Unknown evidence never becomes a negative assertion.
- Attention is derived, never authoritative.
- Generating attention writes **zero** ledger events.
- Running != deployed != authoritative remains intact.
- Foundation 3 reconciliation semantics are unchanged (and stay read-only).
- Foundation 5 artefacts remain immutable.
- Foundation 6 deployment observations remain immutable.

## Commands

```text
clankctl attention
clankctl attention <clank>
clankctl attention --json
clankctl attention --older-than 72h
```

`--older-than` is an operator-supplied threshold for `STALE_OPEN_SESSION`.
The default `24h` is the existing Session-staleness default, not a new
failure age. Output names the source of the threshold.

## Reason codes

Stable codes are part of the output contract. There is no opaque score.

| Code | Class | Meaning |
| --- | --- | --- |
| `MISSION_NO_NEXT_ACTION` | integrity | An unfinished Mission has no recorded next action |
| `GIT_DRIFT` | integrity | Foundation 3 reconciliation status is `drift` |
| `DIRTY_WITHOUT_OPEN_SESSION` | integrity | Locally observable dirty checkout, and this Clank has no open Session |
| `CI_EVIDENCE_BEHIND_MISSION` | integrity | Latest `github_ci` artefact SHA differs from the Mission's recorded HEAD |
| `DEPLOYMENT_DIFFERS_FROM_MISSION` | informational | A current deployment observation's SHA differs from the Mission's recorded HEAD |
| `STALE_OPEN_SESSION` | age | Open Session older than the Session-staleness threshold |

Each item includes Clank, Mission, `reason_code`, plain-English reason,
relevant age/timestamp, evidence identifiers, suggested operator action,
and provenance / source plane.

## What is not a failure

- No CI artefact is not `CI_EVIDENCE_BEHIND_MISSION`. Some Missions have no CI.
- Unobservable local Git is not dirty and not drift.
- Dirty + open Session is not `DIRTY_WITHOUT_OPEN_SESSION`.
- Matching deployed SHA is not `DEPLOYMENT_DIFFERS_FROM_MISSION`.
- Deployment SHA mismatch is **informational**. Wording is
  "deployment differs from recorded Mission HEAD", never "stale",
  "failed", or "outdated deployment".
- Each current deployment surface is compared only with the Mission named
  on that observation (`mission_id`). A surface captured for Mission B is
  never compared with Mission A. Missing `mission_id` is unattributable and
  produces no mismatch assertion.
- Multiple Foundation 6 `surface_id`s remain separately attributable.
- `GIT_DRIFT` names the Foundation 3 contradicted field (`branch` / `head` /
  `working_tree`) and that field's source. It does not always pretend the
  mismatch is HEAD.
- Mission-scoped CI items are evaluated for **every** unfinished Mission.
  Attention never silently selects `unfinished[0]`.

## Freshness ages

The report exposes ages for latest checkpoint, every open Session, CI capture,
and deployment observation. Those ages are not hard-coded into verdicts.
Canonical freshness lists `open_sessions`; it does not pick `open_for[0]`.

Ordering: integrity, then informational, then operator-threshold age items;
oldest first within a class.

## Terminal

Fleet home gains an **ATTENTION** block. Reason codes and explanatory text
are rendered as text plus icon/shape. Colour is not the only signal.

## Out of scope

- Foundation 8
- Mutating Missions or auto-closing Sessions
- Scheduling product Clanks
- SSH / live host inventory
- A unified Clank health score
