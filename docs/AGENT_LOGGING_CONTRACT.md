# Agent logging contract

Future Clank-development agents must log through ClankOps so work survives chat expiry, machine restart, and handoff.

Foundation 0/0.1 provides the primitives and this contract. Foundation 1 injects it into Cursor fleet rules (`Clanks/AGENTS.md` and this repository's `.cursor/rules/clankops-logging.mdc`). It still does **not** patch every Clank repository or collection launcher. Humans should not have to type these logs during ordinary development once launchers pass `CLANKOPS_SESSION_ID`; until then, agents must still write them.

## Required behaviour

1. **Identify the Clank before changing it.** `clankctl show <clank>` or `clankctl list`. If missing, `clankctl register` with path and remote. Do not invent a second identity for a rename; add an alias or update a ref.
2. **Open or resume a Mission.** `clankctl mission start <clank> "<objective>"` or `clankctl mission resume <mission>`. A Mission is a coherent objective, not a chat session. It must survive weeks of inactivity.
3. **Record the actor and Session.** Pass `--actor` (examples: `cursor`, `codex`, `claude`, `glm`, `grok`, `user`). Actor is a string, not a vendor enum. `mission start` / `resume` open a Session and print `session=<uuid>`.

   Attribute later mutations to that Session using **one** of:

   1. `--session <uuid>` on the same `clankctl` invocation
   2. `CLANKOPS_SESSION_ID` in the environment (preferred for launchers)
   3. fallback: if this `--actor` has exactly one open Session on the Mission, ClankOps binds it

   If two agents are working the same Mission, each must pass `--session` or its own `CLANKOPS_SESSION_ID`. Do not assume a Mission has a single active Session. If attribution is unknown (reconstructed history), omit the Session; do not invent one. A supplied Session that belongs to another Mission, Clank, or actor, is closed, or does not exist, is rejected — ClankOps will not silently record the mutation without a Session.

   End work with `clankctl mission pause|block|complete|abandon` (closes all open Sessions on that Mission) or `clankctl session end <session>` for a single actor.
4. **Record meaningful checkpoints**, not spam. At minimum: when stopping, when switching branches, after a mergeable unit of work, after tests. Include completed / current / next / outstanding / tests / branch / HEAD / working-tree. Use `--capture-git` when a local repo exists.
5. **Record new features** when a capability actually appears or is proposed: `clankctl feature add <clank> "<feature>"`.
6. **Record concrete tasks**, not speculative ideas: `clankctl task add <mission> "<task>"` and `clankctl task done <task>`.
7. **Record architectural decisions** with rationale and alternatives: `clankctl decision add <mission> "<decision>" --why "..." --alternatives "..."`.
8. **Record blockers** that actually prevent progress: `clankctl blocker add <mission> "<blocker>"`.
9. **Attach evidence** (commits, branches, PRs, test runs) as artefacts or checkpoint fields. Mark the source honestly: local git vs GitHub vs agent report.
10. **Before stopping**, set Mission state explicitly: complete, pause, block, or abandon. Do not leave an ACTIVE mission by vanishing.
11. **Final checkpoint must contain the exact next action** if one is known. If unknown, omit it. Do not fabricate.

## Provenance rules for agents

- Saying “I pushed `abc123`” is `AGENT_REPORT` until GitHub or local git corroborates it.
- Never copy `.env`, tokens, webhook URLs, or credentials into notes, payloads, or artefacts.
- Never clean, reset, stash, or discard dirty work in another Clank unless the operator explicitly asked. Dirty state is evidence; record it.
- Reconstructed or inferred history must be logged with `source=RECONSTRUCTED` (or `--source RECONSTRUCTED`).

## Suggested command spine

```text
clankctl show <clank> || clankctl register <clank> --path <path>
clankctl mission start <clank> "<objective>" --actor cursor
# export CLANKOPS_SESSION_ID=<printed session uuid>
# ... do the work ...
clankctl --session "$CLANKOPS_SESSION_ID" checkpoint <mission> --completed "..." --current "..." --next "..." --capture-git --tests "pytest: N passed"
clankctl mission pause <mission>
```

On resume:

```text
clankctl brief <clank>
clankctl mission resume <mission> --actor cursor
```

## What ClankOps will not do (yet)

- Auto-start a Session from every dashboard/collection `.cmd` launcher
- Auto-evaluate agent quality
- Summarize history with an LLM
- Modify other Clank repositories to add hooks without an explicit Mission on that Clank

Cursor fleet logging rules are in place as of Foundation 1. Collection `.cmd` launchers remain manual GUI triggers. The ledger is ready to receive logs now.
