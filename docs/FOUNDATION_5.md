# Foundation 5 — CI artefacts on the Mission

Foundation 4 can **observe** GitHub check-runs and status contexts. It does not write that observation into the ledger. Operator review of PR #5 therefore still had to treat live `clankctl reconcile` CI as ephemeral.

Merged Foundation 4 baseline on `main`: `855c1fcc5a28e8274ac9ba4eb26870f6974da64e` (PR #5).

## What it does

- Explicit write: `clankctl ci capture <clank>` attaches the current GitHub CI observation to an unfinished Mission as an artefact (`kind=github_ci`, `source=CI`).
- Optional `--mission COPS-xxxxxx` selects the target when more than one unfinished Mission exists.
- `clankctl reconcile` stays read-only. Capture is a separate command.
- Does **not** rewrite checkpoints, branch/HEAD/working-tree claims, or Foundation 3 git `aligned` / `partial` / `drift`.
- Failing check-run and status-context names are stored in artefact metadata (and in the title when concise) so a later reader can explain the stored CI state without a live GitHub fetch.
- Terminal dossier lists **ARTEFACTS**. Provenance is `CI`, never a silent promotion of `AGENT_REPORT`.

## Mission targeting

`ci capture` attaches only to an unfinished Mission: `PLANNED` / `ACTIVE` / `PAUSED` / `BLOCKED`.

It does **not** use `active_or_unfinished_mission()` (that helper can fall back to a completed or abandoned Mission).

| Unfinished Missions | `--mission` | Result |
| --- | --- | --- |
| none | omitted | fail loudly |
| exactly one | omitted | attach there |
| several | omitted | fail as ambiguous |
| any | explicit unfinished Mission on this Clank | attach there |
| any | explicit Mission on another Clank, or completed / abandoned / superseded | fail |

## SHA binding

`--mission` selects both the artefact destination **and** the CI observation target.

Capture resolves Mission M first, then observes GitHub CI for a SHA attributable to M. It does not use `active_or_unfinished_mission()`, and it does not let the current checkout HEAD override M's recorded HEAD.

| Mission evidence | Live checkout | CI SHA |
| --- | --- | --- |
| recorded checkpoint HEAD | anything | that recorded SHA |
| recorded branch, no HEAD | same branch, not detached | live local HEAD |
| no attributable SHA | anything else | fail: no CI target; no artefact |

`clankctl reconcile` is unchanged: it still uses the brief helper for recorded claims and may inspect the current checkout. Capture passes an explicit Mission into an internal reconcile path and skips that path's CI inspection (`include_ci=False`).

## Evidence

A `github_ci` artefact stores normalized Foundation 4 fields, not raw `gh` output and not secrets:

`repo`, `sha`, `state`, `error`, `checks_observed`, `statuses_observed`, `check_run_total`, `checks_complete`, `status_total`, `statuses_complete`, `combined_state`, `runs`, `contexts`, `failing_runs`, `failing_contexts`, `git_status`.

Binding fields so a later reader can prove the observation belonged to Mission M:

`mission_id`, `mission_display`, `checkpoint_id`, `recorded_branch`, `recorded_sha`, observed CI `sha`, `sha_attribution`.

`git_status` is the Foundation 3 reconciliation display value against **that Mission's** recorded claims. Capture does not mutate it.

A failure produced only by a legacy status context remains auditable from those stored fields.

## Observation gate

- Observed CI states including `unknown` and `none` may be captured, with error and completeness evidence.
- `state is None` because nothing was observed (no GitHub repo, no CI target, both planes unobserved) fails loudly. No artefact is minted.
- A local SHA alone is not a GitHub CI observation.

## Out of scope

- Auto-updating checkpoints from CI
- Owning product-Clank workflows
- Collection scheduling
- Remote SSH / live host inventory (Foundation 6 records captured evidence only)
