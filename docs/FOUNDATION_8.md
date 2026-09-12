# Foundation 8 — Resume packets + agent admission

A development agent should be able to enter an existing Clank with **one
derived packet** of ClankOps facts. The packet is observational. The ledger
and its projections remain authoritative.

This is not an LLM summary. Unknown stays unknown. Missing evidence is not
advice, not failure, and not a health score.

## Law

- The resume packet is **derived**, not authoritative.
- Packet generation writes **zero** ledger events.
- Agent identity is provenance, not permission.
- No implicit Mission creation.
- Multiple unfinished Missions are never silently collapsed.
- Unknown is not negative.
- The context fingerprint is for stale-context detection only. It does not
  prove that a human or agent read or understood the packet.
- Admission uses existing Foundation 1 Session attribution. It does not
  mutate product repositories.

## Commands

```text
clankctl resume-packet <clank>
clankctl resume-packet <clank> --json

clankctl agent prepare <clank> --actor cursor
clankctl agent prepare <clank> --actor codex
clankctl agent prepare <clank> --actor glm
clankctl agent prepare <clank> --actor grok

clankctl agent admit <clank> --mission <mission> --actor <actor>
clankctl agent admit <clank> --mission <mission> --actor <actor> --expect-context sha256:...
```

`--json` is the canonical machine interface. Human text is concise and
operator-friendly. There is no generated prose summary.

`--actor` on `agent prepare` / `agent admit` overrides the global `--actor`.
Actor strings are metadata. Unknown names are not rejected.

`--no-github` skips live `gh` inspection, same as reconcile/attention.

## Packet contents

Derived from existing ClankOps state:

- Clank identity, canonical local path, canonical GitHub identity (Foundation 3
  choice rules; no guessing among ambiguous refs)
- **Every** unfinished Mission (never `unfinished[0]` as “the” Mission)
- Latest checkpoint, including backing event actor/source/provenance
- Foundation 3 reconciliation snapshot (status, comparisons, recorded
  checkpoint/event source, drift, Mission attribution) — observed once
- Latest Foundation 5 `github_ci` artefact **per Mission**, including the
  immutable evidence-binding metadata
- Current Foundation 6 deployment surfaces with Mission attribution and
  authority/provenance fields (no credentials or webhook URLs)
- Open blockers, unfinished tasks, recent decisions for each unfinished Mission
- Current branch / HEAD / working-tree when locally observable
- Evidence timestamps and provenance

Unobservable Git remains unknown. Missing CI is “none recorded”, not failure.

## Admission classification

`agent prepare` produces the same canonical packet and classifies unfinished
Missions. It mutates nothing.

| Unfinished Missions | `admission.status` | Behaviour |
| --- | --- | --- |
| exactly one | `RESUMABLE` | Identify it. Do not open a Session. |
| several | `AMBIGUOUS` | Require explicit `--mission` on admit. |
| none | `NO_UNFINISHED_MISSION` | Use existing `work start` for a new objective. |

`agent admit` requires `--mission`. It may open or resume a Session via
`Store.open_work_session` (Foundation 1 rules). It never calls `work start`.

If `--expect-context` is supplied and the current packet fingerprint differs,
admission fails **before** Session creation.

## Context fingerprint

`context_fingerprint` is `sha256:` plus a SHA-256 of the packet’s ClankOps
facts (stable JSON). Ephemeral ages, requested-actor metadata, and
Foundation 7 `CLASS_AGE` / `STALE_OPEN_SESSION` items are excluded so
wall-clock membership does not churn the hash. Those age items still appear
unchanged in the packet display.

Identical projection and observational facts → identical fingerprint. A
relevant state change (new checkpoint, new Session, new artefact, Git fact)
changes it. Projection rebuild yields an equivalent packet and fingerprint.

Packet generation runs **one** Foundation 3 reconciliation snapshot and
reuses it when deriving Attention for that Clank. Standalone
`clankctl attention` is unchanged.

## Launchers

Thin examples in `examples/agents/` consume the same CLI/JSON contract.
They do not modify Cursor, Codex, GLM, or Grok. They do not add credentials.

`scripts/clankops-dev.ps1` may run `packet`, `prepare`, and `admit` as a thin
wrapper around the same commands.

## Out of scope

- Foundation 9
- LLM summarisation
- A Clank health score
- Mutating product-Clank repositories
- Absorbing Quartermaster, Standards, or Motherclank
- Implicit Mission creation
- Claiming the fingerprint proves comprehension
