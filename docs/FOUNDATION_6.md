# Foundation 6 — Deployment & runtime provenance

ClankOps can record the **actual deployed/runtime state** of a Clank without
confusing it with source-code state or CI state.

This is the first slice. It is not a generic infrastructure orchestrator and
it does not SSH, schedule, or mutate remote hosts. Capture is explicit.

## What landed

- First-class durable **deployment observations** (`DEPLOYMENT_OBSERVED`).
- Explicit write: `clankctl deployment capture <clank> ...`
- Read model: `clankctl deployment list <clank>` and `clankctl deployment current <clank>`
- Terminal dossier section **DEPLOYMENTS** (labels as text, never colour alone)
- Mission targeting uses the same unfinished-Mission laws as Foundation 5
- Secrets (webhook URLs, tokens, credentials) are stripped from every
  user-controlled capture field **before** the event is appended.

## Distinctions

`DEPLOYED != RUNNING != AUTHORITATIVE`.

1. Source HEAD is not deployed HEAD.
2. CI success is not deployment success.
3. Running is not authoritative.
4. Collection authority and notification authority are independent.
5. Multiple simultaneous deployments of one Clank are valid.
6. Historical observations are immutable. A later capture supersedes the read
   model; it does not rewrite history.
7. Unknown stays unknown.
8. Webhook URLs, tokens, and credentials are never stored.
9. Runtime evidence must state how it was observed (`--observed-how`).
10. Reconciliation remains read-only.

## Capture

```text
clankctl deployment capture oem-radar --surface hetzner-prod --environment prod --host ubuntu-4gb-hel1-1 ...
clankctl deployment list oem-radar
clankctl deployment current oem-radar
```

`--surface` is the stable deployment identity (`hetzner-prod`, `nas-canary`,
`experimental-sitemap-soak`). It is not inferred from host, environment,
runtime path, container name, or SHA. Those remain attributes.
`--environment` is one of `prod | staging | canary | dev | experimental`.
`--observed-how` is required. `--mission COPS-xxxxxx` is required when several
unfinished Missions exist.

`source=DEPLOYMENT`. The observer/actor is recorded separately. How the
evidence was obtained is `observed_how` (operator report, agent report, …).
This slice does not connect to Hetzner or NAS.

Unfinished = `PLANNED` / `ACTIVE` / `PAUSED` / `BLOCKED`. Zero unfinished
Missions fails. Several without `--mission` fails. An explicit Mission must
belong to the requested Clank and be unfinished.
`active_or_unfinished_mission()` is not used.

## Read model

Current = latest observation per `(clank_id, surface_id)`. `list` shows
that set; `list --history` shows every observation in ledger order with
`is_current`. Different surfaces on the same host and environment stay
simultaneously current. Projection rows are insert-only; rebuild replays events.

User-controlled strings (host, paths, image, runtime identity, cadence,
state store, observed_how, observer, notes, metadata) pass a secret barrier
before `store._emit()`. Webhook URLs, userinfo, `token=` / `api_key=` query
parameters, Bearer values, and GitHub-style tokens are stripped. Safe
`webhook_configured` flags survive.

## Out of scope

- Remote SSH / inventory automation
- Owning deploy pipelines or schedulers
- Treating CI green as deployed
- Foundation 7
