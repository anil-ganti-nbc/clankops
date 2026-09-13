# Agent logging contract

Future Clank-development agents must log through ClankOps so work survives chat expiry, machine restart, and handoff.

Foundation 0/0.1 is the ledger. Foundation 1 makes Cursor adoption operational via `work` / `handoff` and the shared [Cursor agent contract](CURSOR_AGENT_CONTRACT.md). Dashboard/collection launchers are not wrapped. Humans should not have to type these logs during ordinary development once `scripts/clankops-dev.ps1` has exported a Session; until then, agents must still write them.

## Required behaviour

1. **Identify the Clank before changing it.** `clankctl show <clank>` or `clankctl list`. If missing, `clankctl register` with path and remote. Do not invent a second identity for a rename; add an alias or update a ref.
2. **Open or resume a Mission.** Prefer `clankctl work resume <clank>` so an unfinished Mission is reused. Use `clankctl work start <clank> "<objective>"` only for a new objective (`--new-mission` if one is already unfinished). A Mission is a coherent objective, not a chat session.
3. **Record the actor and Session.** Pass `--actor`. `work start` / `work resume` open a Session, write `%USERPROFILE%\.clankops\contexts\<clank-id>.json`, snapshot `sessions\<session-id>.json`, and print `$env:CLANKOPS_*` assignments. `last-active.json` is a convenience pointer only.

   Attribute later mutations using **one** of:

   1. `--session <uuid>` on the same `clankctl` invocation
   2. `CLANKOPS_SESSION_ID` in the environment (preferred for launchers)
   3. fallback: if this `--actor` has exactly one open Session on the Mission, ClankOps binds it

   If two agents are working the same Mission, each must pass `--session` or its own `CLANKOPS_SESSION_ID`. A supplied Session that belongs to another Mission, Clank, or actor, is closed, or does not exist, is rejected.

   Stop with `clankctl handoff <mission> --state PAUSED|BLOCKED|COMPLETED|ABANDONED` (checkpoint + git capture + Mission state). `session end` remains available for a single actor.
4. **Record meaningful checkpoints**, not spam. Include completed / current / next / outstanding / tests / branch / HEAD / working-tree. `handoff` captures git from the canonical local path.
5. **Record new features** when a capability actually appears: `clankctl feature add <clank> "<feature>"`.
6. **Record concrete tasks**: `clankctl task add` / `task done`.
7. **Record architectural decisions** with rationale.
8. **Record blockers** that actually prevent progress.
9. **Attach evidence** as artefacts. Mark the source honestly: local git vs GitHub vs agent report.
10. **Before stopping**, set Mission state explicitly via `handoff`. Do not leave an ACTIVE mission by vanishing.
11. **Final checkpoint must contain the exact next action** if one is known. If unknown, omit it. Do not fabricate.

## Provenance rules for agents

- Saying “I pushed `abc123`” is `AGENT_REPORT` until GitHub or local git corroborates it.
- Never copy `.env`, tokens, webhook URLs, or credentials into notes, payloads, or artefacts.
- Never clean, reset, stash, or discard dirty work in another Clank unless the operator explicitly asked. Dirty state is evidence; record it.
- Reconstructed or inferred history must be logged with `source=RECONSTRUCTED`.

## Suggested command spine

```text
python -m clankops --actor cursor resume-packet <clank>
python -m clankops --actor cursor agent prepare <clank>
python -m clankops --actor cursor brief <clank>
python -m clankops --actor cursor work resume <clank>
# or, after an explicit Mission choice:
# python -m clankops --actor cursor agent admit <clank> --mission COPS-xxxxxx
# export printed CLANKOPS_*  OR  .\scripts\clankops-dev.ps1 resume <clank>
python -m clankops --actor cursor handoff <mission> --state PAUSED --completed "..." --current "..." --next "..." --tests "pytest: N passed"
```

## What ClankOps will not do (yet)

- Auto-start a Session from dashboard/collection `.cmd` launchers
- Auto-evaluate agent quality
- Summarize history with an LLM
- Modify other Clank repositories to add hooks without an explicit Mission on that Clank

Collection `.cmd` launchers remain manual GUI triggers. See [FOUNDATION_1.md](FOUNDATION_1.md).
