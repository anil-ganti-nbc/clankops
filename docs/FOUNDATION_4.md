# Foundation 4 — GitHub commit-status and CI evidence

Foundation 3 can tell whether recorded git claims match live git/GitHub. It still could not see whether a SHA had **CI**. Operator review of PR #4 therefore had to treat `119 passed` as local evidence only.

Merged Foundation 3 baseline on `main`: `c54387abd9f953fca43a01104ae97665e84e95a5` (PR #4).

## What it does

- GitHub Actions `pytest` on `pull_request` and `push` to `main`. No cron. Not collection. Not a Windows Task Scheduler entry.
- Read-only `gh` observation of check-runs / combined status for a SHA (`source=GITHUB`).
- Terminal RECONCILIATION shows CI check state. Empty checks are `none`, never `success`. GitHub combined `pending`/`success` with zero check-runs and zero status contexts is also `none`.
- CI observation does **not** change git `aligned` / `partial` / `drift` reduction. Observer `ok=True` is still not git corroboration.
- Tests that inject `inspect_remote` never call live `gh` for checks.

## Out of scope

- Auto-updating checkpoints from CI
- Owning product-Clank CI
- Collection scheduling
- Foundation 5
