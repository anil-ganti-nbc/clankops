# Foundation 4 — GitHub commit-status and CI evidence

Foundation 3 can tell whether recorded git claims match live git/GitHub. It still could not see whether a SHA had **CI**. Operator review of PR #4 therefore had to treat `119 passed` as local evidence only.

Merged Foundation 3 baseline on `main`: `c54387abd9f953fca43a01104ae97665e84e95a5` (PR #4).

## What it does

- GitHub Actions `pytest` on `pull_request` and `push` to `main`. No cron. Not collection. Not a Windows Task Scheduler entry.
- Read-only `gh` observation of check-runs **and** status contexts for a SHA (`source=GITHUB`). Roll-up is unified: failure in either source is failure.
- Empty CI is `none` only when **both** observers succeeded and returned no check-runs and no status contexts. GitHub combined `pending`/`success` with zero contexts is not evidence.
- If status contexts (or check-runs) could not be observed, the state is `unknown`, never `none`.
- `success` requires every check-run to be `completed` **and** the observed check-run set to cover GitHub `total_count`. Status contexts are fetched with `per_page=100` plus bounded pagination. A truncated status-context page cannot mint `success` unless GitHub's combined `state` independently proves success for `combined_total > 0` (that aggregate covers every context). Combined `pending`/`success` with zero contexts is still not evidence. Already-observed or aggregate failure/pending still surface.
- CI observation does **not** change git `aligned` / `partial` / `drift` reduction. Observer `ok=True` is still not git corroboration.
- Tests that inject `inspect_remote` never call live `gh` for checks.

## Out of scope

- Auto-updating checkpoints from CI
- Recording CI into the ledger (Foundation 5)
- Owning product-Clank CI
- Collection scheduling
