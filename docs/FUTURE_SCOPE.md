# Future scope

Ideas noticed during Foundation 0. Do not treat this list as a backlog
commitment. Fleet Harvest 1 implemented one-shot local Git harvest and
worktree inventory. Fleet Pulse 1 implemented optional Windows scheduling
of that same command. Remote GitHub freshness remains out of scope.

## Terminal / UI

- Bloomberg-style operator terminal — **Terminal Beta** implements the
  read-only Fleet Command Centre (`clankctl terminal`): snapshot-default
  fleet grid, dossier evidence matrix, attention/sessions views, bounded
  filter grammar, keyboard contract. It still does not mutate the ledger,
  run Harvest, or contact GitHub unless explicitly asked (`?github=1`).
- Native Windows application — **Desktop Alpha** is a thin shell around
  that Terminal (`python -m clankops.desktop` / `ClankOps.exe`): loopback
  `serve()`, WebView2, single-instance mutex, ONEDIR package. It does not
  duplicate Terminal, create authority, or bundle an installer.
- Desktop Beta (deferred): installer, updates, tray, Start Menu,
  optional startup, icon polish, code signing, optional WebView2
  bootstrap.
- Full Clank query language / semantic search — deferred
- Mutation / control UI — deferred; not part of Terminal Beta or Desktop Alpha

## Git / GitHub ingestion

- Periodic local `git` harvest per registered path (still read-only) —
  **Harvest 1** implements one-shot `clankctl harvest local-git` with
  semantic-state dedup, harvest-run freshness, worktree inventory, and
  dry-run. **Fleet Pulse 1** implements optional Windows Task Scheduler
  cadence for that same command (explicit install; default every 10
  minutes; one-shot; no daemon). Remote GitHub freshness is still not
  implemented (no fetch, no GitHub polling).
- GitHub compare against agent-reported SHAs so `AGENT_REPORT` is never silently promoted
- PR/issue attachment from `gh`
- Worktree inventory — **partially implemented** by Harvest 1 as local
  Git evidence attached to the existing Clank; worktrees are not new
  identities and are not independently harvested
- Stale-branch ageing with evidence

## Deployment tracking

- Foundation 6 records explicit deployment/runtime observations (`clankctl deployment capture`); it still does not SSH or own deploy pipelines
- Remote inventory / soak windows / PHASE0 containment labels
- Auto-discovery of hosts

## CI integration

- Foundation 4 observes GitHub check-runs / combined status read-only
- Foundation 5 records that observation as a Mission artefact (`clankctl ci capture`); it still does not own CI
- Auto-updating checkpoints from CI
- Ingest pytest/ruff/mypy outcomes beyond the GitHub check-run summary

## Agent integration

- Foundation 8 emits a derived resume packet (`clankctl resume-packet`,
  `clankctl agent prepare`, `clankctl agent admit`) from existing ledger
  facts. It does not summarise with an LLM, create Missions implicitly, or
  treat agent identity as a permission.
- Foundation 9 is the managed launch gate (`clankctl agent launch`):
  prepare, admit, then spawn. Actor/launcher names remain provenance.
- Foundation 10 records managed process exit (`AGENT_PROCESS_EXITED`)
  without closing the Session. Child exit is not a handoff. Missing
  evidence stays UNKNOWN, never RUNNING.
- Foundation 11 reconciles stale projected Mission state from explicit
  evidence (`MISSION_STATE_RECONCILED`), including stale ACTIVE when no
  Session is open. Session close is not a recorded handoff. PR merge is
  not Mission completion unless reconciled. Ordinary live ACTIVE
  completion remains the cooperative path.
- Inject the [agent logging contract](AGENT_LOGGING_CONTRACT.md) into Cursor/Codex/Claude/GLM/Grok launchers
- Session auto-start from agent identity
- Refuse to start Clank work until `brief` has been read
- Hook scripts / Cursor rules per Clank without absorbing those Clanks

## Clank launcher integration

- `C:\Users\anil\Clanks\_Launchers` is dashboard/collection `.cmd` only
  (inspected in Foundation 9; left unwrapped). `Clanks_\Launchers` does
  not exist.
- Record which *collection* launcher started which session
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

- Foundation 7 derives an attention queue (`clankctl attention`) from existing
  evidence. It does not invent a Clank health score, auto-close Sessions, or
  treat age as failure by default.
- Additional ageing heuristics beyond the initial reason codes
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
