# Foundation 5 — CI artefacts on the Mission

Foundation 4 can **observe** GitHub check-runs. It does not write that observation into the ledger. Operator review of PR #5 therefore still had to treat live `clankctl reconcile` CI as ephemeral.

Merged Foundation 4 baseline on `main`: `855c1fcc5a28e8274ac9ba4eb26870f6974da64e` (PR #5).

## What it does

- Explicit write: `clankctl ci capture <clank>` attaches the current GitHub CI observation to the unfinished Mission as an artefact (`kind=github_ci`, `source=CI`).
- `clankctl reconcile` stays read-only. Capture is a separate command.
- Does **not** rewrite checkpoints, branch/HEAD/working-tree claims, or Foundation 3 git `aligned` / `partial` / `drift`.
- Failing check-run names are included in the artefact title so they are attached to the open Mission without ClankOps owning CI.
- Terminal dossier lists **ARTEFACTS**. Provenance is `CI`, never a silent promotion of `AGENT_REPORT`.

## Completeness

Capture stores the Foundation 4 completeness fields (`check_run_total`, `checks_complete`, `status_total`, `statuses_complete`) in artefact metadata. It does not invent a greener CI state than observation allowed.

## Out of scope

- Auto-updating checkpoints from CI
- Owning product-Clank workflows
- Collection scheduling
- Foundation 6
