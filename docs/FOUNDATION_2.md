# Foundation 2 — fleet coverage + Terminal alpha

Foundation 1 made Cursor actually log through ClankOps. Foundation 2 puts the rest of the **VERIFIED** census fleet into the ledger and puts a **read-only** Terminal in front of the projections.

Merged Foundation 1 baseline on `main`: `5dd9e1c79be45be1b15098ea2eb0a75ffaf48c48` (PR #2).

## Fleet coverage

```text
python -m clankops --actor cursor fleet-adopt-verified --file data/bootstrap/clank_census.json
```

- Registers missing `VERIFIED` census identities (`source=RECONSTRUCTED`).
- Existing slugs/remotes are reused; extra checkouts become additional `local_path` refs.
- `SUPPORT_COMPONENT` copies of a VERIFIED remote are extra refs, not a second Clank.
- `(github-only)` is not stored as a filesystem path.
- Adjacent products such as `clank-ledger` may appear as named identities; they are not absorbed into ClankOps.

Does not rewrite census history, does not clean dirty trees, does not start product feature work.

## Terminal alpha

```text
python -m clankops terminal
```

Binds `127.0.0.1:8765` by default. GET/HEAD only. Opens the ledger with `mode=ro` / `query_only`. No collection, no Task Scheduler, no Hetzner.

Routes: `/`, `/clank/<slug>`, `/api/fleet`, `/api/clank/<slug>`, `/health`.

## Out of scope

- Product feature work on OEM Radar / SI / Watch / CTW
- Full Bloomberg terminal
- GitHub CI ingestion
- Stale-session scoring
- Remote binds / production hosting
