# Terminal Beta — Fleet Command Centre

Read-only operator cockpit for recorded ClankOps state.

The Terminal presents evidence. It does not create authority. It is not
Motherclank, Standards Clank, Quartermaster, a deployment controller, a
Mission state machine, a Git client, a CI controller, or a repair tool.

## Authority boundary

Browser GET/HEAD only. POST/PUT/PATCH/DELETE return 405.

Loading a page does not:

- run Fleet Harvest
- git fetch / pull
- contact GitHub (unless `?github=1`)
- inspect live local git (unless `?live=1`)
- SSH, capture CI, inspect deployment hosts
- create Events, Missions, Sessions, or Harvest observations

If Harvest evidence is missing or stale, the UI may hint:

```text
clankctl harvest local-git <clank>
```

The browser never executes that.

No buttons for complete/pause/resume/reconcile/handoff/deploy/rerun CI/
run Harvest/stash/commit/checkout/kill agent.

## Snapshot vs live vs GitHub

Default pages are a **snapshot** of already-recorded projections:

```text
[SNAPSHOT]
```

`?live=1` permits Foundation 3 read-only live **local** git inspect:

```text
[LIVE LOCAL]
```

`?github=1` permits read-only GitHub evidence retrieval:

```text
[GITHUB NETWORK]
```

Harvested `LOCAL_GIT` is not live. Live Foundation 3 observations are not
Fleet Harvest. The two planes stay labelled separately.

A snapshot marker is attached to top-level payloads:

- `generated_at`
- `ledger_event_count`
- `max_ledger_seq`

This is **read-time**, not a transactionally frozen multi-page snapshot.
Two tabs opened seconds apart may see different `max_ledger_seq` if
another actor wrote.

## Routes

HTML:

| Path | Purpose |
| --- | --- |
| `/`, `/fleet` | Fleet command centre |
| `/clank/<slug>` | Per-Clank dossier |
| `/attention` | Derived attention queue |
| `/sessions` | Session / process / handoff evidence |
| `/health` | Process liveness JSON (not a fleet health score) |

JSON (additive fields; existing keys kept):

| Path | Purpose |
| --- | --- |
| `/api/fleet` | Fleet payload + snapshot/mode |
| `/api/clank/<slug>` | Dossier payload |
| `/api/coverage` | Census coverage |
| `/api/sessions` | Open and closed Sessions |
| `/api/sessions/open` | Open Sessions |
| `/api/sessions/stale` | Stale Sessions |
| `/api/attention` | Derived attention |
| `/api/reconcile` | Live reconcile (honours `?live=` / `?github=`) |
| `/api/clank/<slug>/reconcile` | Per-Clank reconcile |

Filters on `/attention`: `clank`, `class`, `reason` / `reason_code`.

Dossier timeline: `limit` default 200, maximum 1000. Display is newest
first; `ledger_seq` remains canonical order.

## Filter grammar

Command bar (`/` to focus). Bounded tokens plus free text. Multiple
structured filters are AND. Unknown keys and invalid values fail visibly
and do not silently return the full fleet.

```text
state:active|paused|blocked|planned|completed|abandoned|superseded
session:open|none|stale
attention:any|integrity|informational|age|none
git:dirty|clean|unknown          # harvested dirty, not live git
harvest:observed|failed|never
clank:<slug>
mission:COPS-000123
```

Unqualified words search slug, display name, Mission id/objective,
recorded next action, task titles, feature names, decision statements.

No regex. No SQL interpolation. No FTS5. No vector search.

## Keyboard

Disabled while typing in input/textarea/select. Ctrl/Alt/Meta modifiers
do not fire plain shortcuts. Tab and ordinary links still work.

| Key | Action |
| --- | --- |
| `/` | focus command/filter bar |
| `j` / `k` | next / previous fleet row |
| Enter | open selected Clank dossier |
| `g` then `f` | fleet |
| `g` then `a` | attention |
| `g` then `s` | sessions |
| `r` | reload current read-only snapshot (does not enable live/GitHub) |
| `?` | help overlay |
| Esc | clear help / command focus |

Selected rows use an inset marker plus class `selected`, not colour alone.

## Evidence planes

Never collapse checkpoint HEAD, harvested HEAD, live HEAD, GitHub HEAD,
CI SHA, and deployed SHA into one “truth”.

Labels:

`[REC]` `[LOCAL_GIT]` `[LIVE LOCAL]` `[GITHUB]` `[CI]` `[DEPLOY]` `[USER]` `[SYSTEM]`

UNKNOWN stays UNKNOWN. A different deployed SHA is informational, not
failure. Dirty is not blocked. Clean is not complete. Old is not wrong.
No canonical `local_path` is not abandoned. Process EXITED with Session
OPEN is not a handoff. Session CLOSED without `HANDOFF_RECORDED` stays
UNKNOWN.

## Attention

Foundation 7 semantics. Derived. Writes zero events. No
acknowledge/dismiss.

Classes keep shape identity: integrity `■`, informational `◇`, age `○`.

## Harvest

Fleet Harvest 1 is displayed, never triggered. Latest check failure
preserves last known good state. Source is `LOCAL_GIT` only after an
observation exists.

## Sessions / process / handoff

Show OPEN/CLOSED, stale, managed process UNKNOWN/EXITED/START_FAILED,
exit code, and handoff RECORDED/MISSING/UNKNOWN as separate facts.
Never collapse them to “finished”.

## Deployments / CI

Multiple surfaces stay multiple surfaces. CI artefacts stay Mission-bound
observations. Terminal does not re-run CI or infer deployment or Mission
completion.

## Localhost

Default bind `127.0.0.1`. Non-loopback `--host` requires `--allow-remote`
and still has no authentication. Fail closed without that flag.

Each HTTP worker opens its own read-only SQLite connection and closes it
before return. Connections are not shared across `ThreadingHTTPServer`
workers.

## Response security

HTML-escape operator/ledger strings. Headers:

- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- CSP allowing only inline style/script (no CDN)

Existing redaction is not a licence to echo secrets. Credentialled Git
URLs, webhook URLs, and token-shaped strings must not appear.

## What Terminal Beta will not do

- Invent a next action
- Invent a health / fleet / Mission score
- Use colour as the only state signal
- Silently poll GitHub or live git
- Mutate the ledger from the browser

## Limits / deferred

- Full Clank query language
- Vector / semantic search
- FTS5
- Auto-refresh (optional later; snapshot endpoints only)
- Mutation / control UI
- Playwright/Selenium browser-driver suite
