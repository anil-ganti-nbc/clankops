# Foundation 1 — live fleet adoption

Foundation 0.1 is the ledger. Foundation 1 makes Cursor-based Clank development actually use it.

Merged `main` baseline this mission started from: see git history (`Merge pull request #1`).

## Adoption law

> Any new Clank development started after Foundation 1 adoption must have a ClankOps Mission before meaningful code changes begin.

> Any interrupted development session must leave a checkpoint and explicit Mission state whenever the agent remains capable of doing so.

Crashes and killed processes are not violations. A future stale-session detector may notice Sessions that disappear without closure. This foundation does not score agents.

## Launcher architecture (inspected)

`C:\Users\anil\Clanks\_Launchers` contains **dashboard and collection** `.cmd` files. They pin a repo path, refuse missing checkouts, and start GUIs or `scripts\run-collection.cmd`. They are not Cursor launchers. Foundation 1 does **not** inject `CLANKOPS_SESSION_ID` into collection.

Cursor integration is a separate development workflow:

```text
python -m clankops --actor cursor brief <clank>
python -m clankops --actor cursor work resume <clank|mission>
# export printed env / scripts/clankops-dev.ps1 resume <clank>
# agent works and checkpoints
python -m clankops --actor cursor handoff <mission> --state PAUSED --completed "..." --current "..." --next "..."
```

PowerShell helper: `scripts/clankops-dev.ps1`. It locates Python 3.14, imports `clankops`, and fails with a red `CLANKOPS INTEGRATION FAILED` banner plus recovery text. It does not block opening the editor.

## Session environment

Per-Clank context (not a single global file):

```text
%USERPROFILE%\.clankops\
    contexts\<clank-id>.json
    contexts\<clank-id>.env
    sessions\<session-id>.json
    last-active.json          # convenience pointer only; never proof of workspace identity
```

`work start` / `work resume` / `checkpoint` write the owning Clank's context plus an immutable Session snapshot. `handoff` keeps the snapshot, marks that Clank's context as ended, and stops advertising `CLANKOPS_SESSION_ID` as active. `work env` identifies the expected Clank from an explicit slug, exported `CLANKOPS_CLANK_ID`/`SESSION_ID`, or the current registered checkout path. It never succeeds merely because some other Clank has a valid last-active Session.

PowerShell: `scripts/clankops-dev.ps1`. Dot-source it so `CLANKOPS_*` lands in the current shell. `resume` and `start` load env from the JSON payload, then `work env`. `-LaunchCursor` opens the canonical target Clank path (not ClankOps unless ClankOps is the Session Clank). After `handoff`, Session/Mission/Clank variables are cleared. Pass `-Database` for an alternate ledger (avoid `-Db`; it collides with PowerShell `-Debug`). Python 3.14+ is compared as a real version, not a string.

## Failure / recovery

| Failure | Behaviour |
| --- | --- |
| `clankctl` not on PATH | Use `python -m clankops` from the clankops checkout (`PYTHONPATH=src` or editable install) |
| Wrong Python | `scripts/clankops-dev.ps1` refuses anything below 3.14 |
| Missing DB / import | Visible error; emergency work allowed |
| Invalid / closed / wrong-Clank Session | `work env` and attributed mutations fail with `CLANKOPS INTEGRATION FAILED` / `ValidationError`. No silent drop. |
| Dirty target repo | Recorded as evidence. Never cleaned by ClankOps. |

Recovery: `python -m clankops --actor cursor work resume <clank>`

## Handoff workflow

`clankctl handoff <mission> --state PAUSED|BLOCKED|COMPLETED|ABANDONED` plus the usual checkpoint flags. Git is captured from the **canonical** `local_path` ref (duplicate checkouts are extra refs, not a second Clank).

## Crash survivability

Cooperative handoff is still required when the agent can respond. Independently, Session start is an event, and each checkpoint refreshes on-disk context. Full stale-work automation is out of scope.

## Identity

Duplicate checkouts (for example Desktop `Watch clank\watch-clank`, OEM Radar git worktree under `_RedditAdmission`) attach as additional `local_path` refs on the canonical Clank. They are not registered as a second product. Census history is not rewritten.

## What Foundation 1 does not do

- Implement OEM Radar / SI / Watch Clank / CTW product features
- Modify collection `.cmd` launchers
- Deploy to Hetzner
- Create Task Scheduler entries
- Merge its own PR
