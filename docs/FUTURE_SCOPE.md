# Future scope

Ideas noticed during Foundation 0. None of these are implemented. Do not treat this list as a backlog commitment.

## Terminal / UI

- Bloomberg-style operator terminal (the original ClankOpsDashboard placeholder)
- Mission/checkpoint timeline views
- Keyboard-first brief pane
- Local web UI for read-only browsing of projections

## Git / GitHub ingestion

- Periodic local `git` harvest per registered path (still read-only)
- GitHub compare against agent-reported SHAs so `AGENT_REPORT` is never silently promoted
- PR/issue attachment from `gh`
- Worktree inventory
- Stale-branch ageing with evidence

## Deployment tracking

- Record staging/prod SHA, host, compose file, and soak window as artefacts
- Distinguish “repo says X” from “host is running X”
- PHASE0 containment / UNVERIFIED_PRODUCTION labels as first-class lifecycle overlays

## CI integration

- Foundation 4 observes GitHub check-runs / combined status read-only
- Foundation 5 records that observation as a Mission artefact (`clankctl ci capture`); it still does not own CI
- Auto-updating checkpoints from CI
- Ingest pytest/ruff/mypy outcomes beyond the GitHub check-run summary

## Agent integration

- Inject the [agent logging contract](AGENT_LOGGING_CONTRACT.md) into Cursor/Codex/Claude/GLM/Grok launchers
- Session auto-start from agent identity
- Refuse to start Clank work until `brief` has been read
- Hook scripts / Cursor rules per Clank without absorbing those Clanks

## Clank launcher integration

- Index `C:\Users\anil\Clanks\_Launchers` (actual location; `Clanks_\Launchers` does not exist)
- Record which launcher started which session
- Guard against double-click / concurrent collection via launcher metadata (observe only)

## Standards integration

- Observe Standards Clank ratification; do not enforce
- Link decisions in ClankOps to Standards decision records

## Motherclank integration

- Consume fleet harvest as `source=SYSTEM` evidence
- Never take over fleet laws or scheduling

## Quartermaster integration

- Link model/quota decisions to Missions that spent tokens
- The current desktop Quartermaster is a wrapper around `token-stats` (upstream Annihilater) — treat as support substrate, not Clank identity

## Search / query

- Structured query language over events
- Full-text over checkpoint notes / decisions
- Vector/semantic search (explicitly deferred)

## Dependency graph

- Materialise relationship kinds into a graph view
- Duplicate-checkout edges from census

## Stale-work detection

- Missions ACTIVE/PAUSED with no checkpoint inside N days
- Dirty trees with no open Mission
- Documents/Default Project checkouts that lag primary Clanks HEAD

## Historical reconstruction

- Import git log / PR history as `RECONSTRUCTED` events
- Mission archaeology from HANDOFF.md / PROGRESS files
- Do not pretend reconstructed times were observed live

## Analytics

- Time-in-state for missions
- Agent/session counts
- No unified “Clank health score” (that is not ClankOps’s job)

## Adjacent discoveries (do not absorb)

- **clank-ledger** (GitHub): editorial HIT/MISS evidence plane — different product; its README already says it is not ClankOps
- **CVC Clank**: institutional incident/learning memory — different from development-state memory
- **DAU labs / clank-systems-handbook**: educational ecosystem, not production Clanks
- **Clank Ledger vs ClankOps naming collision**: keep the names distinct in UI copy later
