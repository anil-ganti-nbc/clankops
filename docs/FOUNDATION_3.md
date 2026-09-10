# Foundation 3 — Git / GitHub evidence reconciliation

Foundation 2 remembered what agents recorded. Foundation 3 observes what is actually on disk and on GitHub, then **compares**.

Merged Foundation 2 baseline on `main`: `0b1e1933a2ed727c38d9b35fc9934c57e4078963` (PR #3).

## Status law

A source with `ok=True` is not corroboration.

`aligned` may only be emitted when **every recorded claim** that Foundation 3 reconciles (`branch`, `HEAD`, `working_tree` when present) has been independently compared and none contradict it.

| Status | Meaning |
|---|---|
| `drift` | At least one comparable fact contradicts the recorded state |
| `aligned` | All relevant recorded facts were independently observed and match |
| `partial` | At least one recorded fact was corroborated, but one or more cannot currently be checked |
| `no-record` | No recorded branch/HEAD/working-tree claims |
| `unknown` | Recorded claims exist but no useful independent comparison could be made |

JSON exposes `comparisons` per field: `corroborated` / `contradicted` / `unobservable` / `not_recorded`, with `source` `LOCAL_GIT`, `GITHUB`, or null.

GitHub-only default-branch checkpoints can corroborate recorded branch/HEAD against GitHub default-branch HEAD. Feature branches need a matching open PR; repository metadata loading is not evidence.

GitHub repository selection is deterministic and does not follow query order: canonical `github_repo`, then canonical GitHub `git_remote`, then a unique remaining GitHub identity. Conflicting non-canonical remotes are reported as ambiguity, not guessed.

## Commands

```text
python -m clankops --json reconcile clankops
python -m clankops --json reconcile --all --no-github
```

- **LOCAL_GIT** is a fresh `inspect_git` of the registered `local_path`. No `git fetch`, no clean, no checkout.
- **GITHUB** is GET-only `gh`. No PR edits, no pushes.
- History is not rewritten. Terminal GET/HEAD still cannot append events.

## Terminal

Fleet evidence column: `[aligned]` / `[drift]` / `[partial]` / `[no-record]` / `[unknown]`.

Dossier **RECONCILIATION** wording follows status. It never says live observation matches recorded claims merely because the drift list is empty.

## Out of scope

- Auto-updating checkpoints to match live git
- `git fetch` / cleaning dirty trees
- Closing stale Sessions
- Foundation 4 CI ingestion
