# Foundation 10 — Managed agent exit observability

Foundation 9 can prove which agent launched, which Mission it entered,
which Session belongs to it, and which resume context it acknowledged.

When the child process exits, ClankOps must be able to distinguish:

- the agent is still running, from
- the managed process exited but its Session was never handed off

without treating process lifecycle as Mission/Session lifecycle.

## Law

**Process exit is evidence. Process exit is not handoff.**

ClankOps never automatically:

- closes the Session
- pauses, completes, or blocks the Mission
- creates a checkpoint
- invents `next_action`
- claims work was completed
- claims the agent handed off

Foundation 1 `handoff` remains the only way to close work. Existing
Foundation 9 fresh-session law is unchanged.

Zero-conflation:

1. Session open ≠ process running.
2. Process exited ≠ Session closed.
3. Process exit 0 ≠ Mission completed.
4. Process exit non-zero ≠ Mission failed.
5. Missing process evidence ≠ process running (it is **UNKNOWN**).
6. Handoff remains an explicit operator/agent action.
7. Attention remains derived.
8. Unknown stays unknown.

Do not call the Session “dead”. Do not introduce a Clank health score.

## Events

`SESSION_STARTED` remains Session authority. There is no second Session
system.

| Event | When |
| --- | --- |
| `AGENT_PROCESS_EXITED` | The managed child was created and the parent observed it return |
| `AGENT_PROCESS_START_FAILED` | Admission succeeded but the subprocess could not be created |

There is no `AGENT_PROCESS_STARTED`. `SESSION_STARTED` already records
that the launch Session began.

Both events are immutable observations scoped to the exact
`session_id` / `mission_id` / `clank_id`. History is append-only. A later
observation on the same Session does not overwrite earlier rows.

`AGENT_PROCESS_EXITED` payload recovers:

- session, mission, clank
- actor, launcher, context fingerprint
- executable basename, argv count, argv_redacted flag
- exit code
- observed timestamp
- observation source / how (`parent_wait`, recorder `clankops.launch`)

`AGENT_PROCESS_START_FAILED` is the same shape minus `exit_code`, plus
the exception **class name** only (`FileNotFoundError`). It is never
recorded as `PROCESS_EXITED`.

ClankOps does not persist full environment, credentials, tokens, prompts,
pasted source, webhook URLs, argv lists, or a reconstructed shell string.

Raw child argv is execution input, not output evidence. `launch_agent()`
and `clankctl agent launch --json` expose only safe command identity
(`executable`, `argv_count`, `argv_redacted`). The child is still invoked
as `subprocess.run(argv, ..., shell=False)`.

## Launch boundaries

| Case | Behaviour |
| --- | --- |
| A. Admission fails | no process, no process event |
| B. subprocess cannot be created | Session may already exist; `AGENT_PROCESS_START_FAILED`; never `EXITED` |
| C. child returns 0 | `EXITED`; Session remains OPEN |
| D. child returns non-zero | `EXITED` with that code; Session remains OPEN; not Mission failure |
| E. ClankOps dies before observing exit | UNKNOWN remains UNKNOWN; no reconstructed exit |

## Derived Session view

After `AGENT_PROCESS_EXITED`:

```text
managed_process: EXITED
session: OPEN
handoff: MISSING
```

See [Foundation 11](FOUNDATION_11.md) for Session closed vs handoff recorded.

Missing evidence is `UNKNOWN`, never `RUNNING`. Start-failure is
`START_FAILED`, not exit. Canonical Foundation 1 `handoff` (checkpoint,
then leaving ACTIVE work) can prove `handoff: RECORDED`. A plain Session
end is `handoff: UNKNOWN`. Process exit never closes the Session and does
not delete historical observations.

Surfaces: `clankctl resume-packet`, `clankctl attention`, `clankctl sessions open`,
Session read model, Terminal dossier. There is no process-control plane
(no kill/restart) and no dedicated `process list` command in this slice.

Display age must not change `context_fingerprint`. A new process
observation must.

## Attention

Foundation 7 gains one derived reason:

`MANAGED_PROCESS_EXITED_WITH_OPEN_SESSION`

Trigger only when a managed Foundation 9 launch Session has **exit**
evidence and that Session is still open. Class is informational, not
age. Existing Foundation 7 reason codes are unchanged.

The item names Mission, Session, actor/launcher, exit timestamp/age, and
exit code, and suggests an explicit Foundation 1 handoff or Session end.
Generating attention writes **zero** ledger events. Closing the Session
through canonical semantics removes the item.

Start-failure does not produce this reason.

## Terminal

Text plus icon/shape, never colour alone:

```text
SESSION 01…
actor: cursor
launcher: cursor
process: EXITED (code 0, 14m ago)
session: OPEN
handoff: MISSING — explicit handoff required
```

## Rebuild

`agent_process_observations` is a projection. `clankctl rebuild` replays
`ledger_seq` order and must produce equivalent state.

## Out of scope

- Foundation 12
- Auto-complete / auto-close on child exit
- A second handoff system
- Treating actor/launcher as a permission
- Process kill/restart
- A Clank health score
- LLM summarisation
- Wrapping dashboard/collection `_Launchers`
