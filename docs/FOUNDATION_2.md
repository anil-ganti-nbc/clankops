# Foundation 2 — fleet coverage + Terminal alpha

Foundation 1 made Cursor actually log through ClankOps. Foundation 2 puts the rest of the **VERIFIED** census fleet into the ledger and puts a **read-only** Terminal in front of the projections.

Merged Foundation 1 baseline on `main`: `5dd9e1c79be45be1b15098ea2eb0a75ffaf48c48` (PR #2).

## Fleet coverage

```text
python -m clankops --actor cursor fleet-adopt-verified --file data/bootstrap/clank_census.json
python -m clankops --json coverage --file data/bootstrap/clank_census.json
```

- Registers missing `VERIFIED` census identities (`source=RECONSTRUCTED`).
- Existing slugs/remotes are reused; extra checkouts become additional `local_path` refs.
- `SUPPORT_COMPONENT` copies of a VERIFIED remote are extra refs, not a second Clank.
- `(github-only)` is not stored as a filesystem path.
- Adjacent products such as `clank-ledger` may appear as named identities; they are not absorbed into ClankOps.
- `coverage` is read-only. It does not promote `PROBABLE` / `UNKNOWN` candidates.
- The VERIFIED denominator is the census artefact (17 identities). ClankOps is additional fleet state (`PROBABLE` in the census) and is not part of that denominator.

Does not rewrite census history, does not clean dirty trees, does not start product feature work.

## Session observability (read-only)

```text
python -m clankops --json sessions open
python -m clankops --json sessions stale --older-than 24h
```

- Open Session: `ended_utc IS NULL`.
- Stale Session: an open Session whose age, according to ClankOps' clock, is at least the threshold (default **24h**).
- These commands never close, pause, abandon, or otherwise mutate Sessions or Missions.
- An open Session attached to a non-ACTIVE Mission is reported as an integrity anomaly. Foundation 0.1 should normally make that state impossible.

## Terminal alpha

```text
python -m clankops terminal
```

Binds `127.0.0.1:8765` by default. GET/HEAD only. Each HTTP request opens its own read-only SQLite connection (`mode=ro` / `query_only`) and closes it afterwards. `ThreadingHTTPServer` is used only so slow requests do not block the listen loop; connections are never shared across threads, and SQLite's same-thread check stays enabled.

Default stale threshold on the fleet home: **24h** (`--stale-after`).

Routes: `/`, `/clank/<slug>`, `/api/fleet`, `/api/clank/<slug>`, `/api/coverage`, `/api/sessions/open`, `/api/sessions/stale`, `/health`.

No collection, no Task Scheduler, no Hetzner, no remote bind.

## Out of scope

- Product feature work on OEM Radar / SI / Watch / CTW
- Full Bloomberg terminal
- GitHub CI ingestion
- Automatically closing stale Sessions
- Remote binds / production hosting
- Foundation 3
