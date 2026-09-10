# Foundation 3 — Git / GitHub evidence reconciliation

Foundation 2 remembered what agents recorded. Foundation 3 observes what is actually on disk and on GitHub, then **compares**.

Merged Foundation 2 baseline on `main`: `0b1e1933a2ed727c38d9b35fc9934c57e4078963` (PR #3).

## What it does

```text
python -m clankops --json reconcile clankops
python -m clankops --json reconcile --all --no-github
```

For each Clank:

- **Recorded claims** come from the latest checkpoint (`branch`, `HEAD`, `working_tree`) and any `payload.git_evidence` on that event (`source=LOCAL_GIT` vs the checkpoint's `AGENT_REPORT`).
- **LOCAL_GIT** is a fresh `inspect_git` of the registered `local_path`. No `git fetch`, no clean, no checkout.
- **GITHUB** is GET-only `gh` (`pr list`, `repos/{owner}/{repo}`, default-branch commit SHA). No PR edits, no pushes.

Drift is surfaced. History is not rewritten. Terminal GET/HEAD still cannot append events.

## Terminal

Fleet home adds an evidence column (`[aligned]` / `[drift]` / `[no-record]` / `[unknown]`) using local git only.

Dossier **RECONCILIATION** shows recorded vs live LOCAL_GIT vs GITHUB, with provenance as text.

`GET /api/reconcile` and `GET /api/clank/<slug>/reconcile`. Pass `?github=1` on the fleet API to include GitHub (off by default so the home page stays local).

## Out of scope

- Auto-updating checkpoints to match live git
- `git fetch` / cleaning dirty trees
- Closing stale Sessions
- Foundation 4 CI ingestion
