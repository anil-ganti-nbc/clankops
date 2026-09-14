# Foundation 9 — Launcher admission gate

Make the Foundation 8 prepare/admit contract the normal entry path for
managed agent work. ClankOps should be able to answer:

- which agent started this work
- which Mission it entered
- which resume packet it acknowledged
- which Session belongs to that launch

Actor and launcher identity are **provenance, not authentication or
permission**.

## Law

No managed Clank agent launch occurs before:

1. a resume packet is generated
2. a Mission is selected explicitly when needed
3. the context fingerprint is acknowledged
4. admission succeeds
5. a Session ID is returned

Only then may the launcher start the agent process.

Fail closed **before** spawning the external process when:

- prepare fails
- admission is `AMBIGUOUS` without an explicit Mission
- there is no unfinished Mission (`NO_UNFINISHED_MISSION`)
- the context fingerprint changed
- the Mission belongs to another Clank
- actor or launcher is empty
- admit fails
- the returned Session identity is missing
- `--command` is missing
- the selected Mission already has an open Session for the same actor
  (managed launch will not reuse it or overwrite its provenance)

Never silently pick a Mission. Never create a Mission from the launch
path. Never partially admit and then continue after an error.

Managed launch must never claim launcher or fingerprint provenance that
is absent from the Session it returns. If an open Session already exists
for that actor, refuse **before** spawn. Do not auto-close it. Do not
mutate it. Tell the operator to hand off or end it through Foundation 1.
Raw `agent admit` may still reuse an open Session (Foundation 8).

Child process exit is **not** a handoff. It does not auto-complete the
Mission, auto-close the Session, invent next actions, or fabricate a
checkpoint. Existing Foundation 1 `handoff` / checkpoint semantics remain
canonical. Missing handoffs remain visible through Session / Attention
observability.

## Commands

```text
clankctl agent prepare <clank> --actor <actor> --json

clankctl agent admit <clank> --mission COPS-xxxxxx --actor <actor>
clankctl agent admit <clank> --mission COPS-xxxxxx --actor <actor> --expect-context sha256:... --launcher <launcher>

clankctl agent launch <clank> --actor <actor> --launcher <launcher> --command <argv...>
clankctl agent launch <clank> --mission COPS-xxxxxx --expect-context sha256:... --command <argv...>
```

`--json` is the canonical machine interface.

`agent prepare` remains zero-write. `agent admit` remains the Session
gate and still does not create a Mission. `agent launch` is the managed
entry: prepare, classify, admit, then `subprocess` with a list argv
(`shell=False`).

| Prepare status | Launch behaviour |
| --- | --- |
| `RESUMABLE` | Wrapper may use that Mission if `--mission` is omitted. |
| `AMBIGUOUS` | `--mission` is required. Never a silent pick. |
| `NO_UNFINISHED_MISSION` | Stop. Use `work start` for a new objective. |

Put `--command` last so child flags stay in argv.

## Provenance

Launcher identity is stored on `SESSION_STARTED` payload/provenance and
projected onto `sessions` (`launcher`, `context_fingerprint`, `source`).
There is no parallel launcher database. Rebuild projections from events
to recover:

- `session_id`
- `mission_id` / display ID
- actor
- launcher identity
- admission context fingerprint
- event source
- launch timestamp (`started_utc`)

Managed launch creates that Session with an atomic **fresh-or-fail**
write (`BEGIN IMMEDIATE`) scoped to the selected Mission and actor. It
does not prove freshness by counting `SESSION_STARTED` events across the
ledger. An unrelated Session starting elsewhere cannot fail a valid
launch. Two concurrent launches for the same Mission and actor yield
exactly one Session and one spawn; the other refuses before spawn.

Raw `agent admit` without `--launcher` still works (nullable launcher
column). Foundation 1 still reuses an already-open Session for the same
actor on `agent admit` / `work resume`. **Managed `agent launch` does
not.** It requires a fresh `SESSION_STARTED` for that launch, carrying
actor, launcher, context fingerprint, source, mission, and session id.
Provenance is verified from **that Session's own** `SESSION_STARTED`
event. The projected Session must match the child environment exactly.

## Environment contract

After successful admission the child receives only non-secret identifiers,
including:

```text
CLANKOPS_CLANK
CLANKOPS_MISSION
CLANKOPS_MISSION_ID
CLANKOPS_SESSION_ID
CLANKOPS_CONTEXT_FINGERPRINT
CLANKOPS_ACTOR
CLANKOPS_LAUNCHER
```

Existing `CLANKOPS_CLANK_ID`, `CLANKOPS_CLANK_SLUG`,
`CLANKOPS_MISSION_DISPLAY`, `CLANKOPS_DB`, `CLANKOPS_CLANK_PATH`, and
`CLANKOPS_CONTEXT_FILE` remain. ClankOps does **not add, print, or
persist** credentials, API keys, tokens, webhook URLs, or provider
secrets. The child process inherits the parent environment (`os.environ`)
for ordinary process needs such as `PATH`; that inherited environment is
a different boundary from ClankOps overlay identifiers.

## Wrappers

Thin examples in `examples/agents/` (Cursor, Codex, GLM, Grok) and
`scripts/clankops-dev.ps1` call the same Python gate. They do not
reimplement `AMBIGUOUS` / `RESUMABLE` classification. They do not add
credentials. Agent argv is passed as an array (`-AgentCommand`), never
interpolated into a shell string.

`work resume` / `-LaunchCursor` remains a separate Foundation 1 path.

## `_Launchers`

Inspected `C:\Users\anil\Clanks\_Launchers`. That tree is
dashboard/collection `.cmd` files. It is not a Cursor/Codex/GLM/Grok
agent launcher. `C:\Users\anil\Clanks_\Launchers` does not exist.

Those collection launchers stay unwrapped (Foundation 1). ClankOps does
not absorb launcher ownership.

## Out of scope

- Foundation 12
- Treating actor/launcher as a permission
- Auto-complete / auto-close on child exit
- A second handoff system
- LLM summarisation
- A Clank health score
- Mutating product-Clank repositories
- Wrapping dashboard/collection `_Launchers`
