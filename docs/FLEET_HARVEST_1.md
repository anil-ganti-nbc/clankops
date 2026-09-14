# Fleet Harvest 1 — automatic local Git evidence

This is the first post-Foundation operational tranche. It is **not**
Foundation 12. The Foundations established the laws. Fleet Harvest
starts feeding those laws with independently observed local evidence.

## Observation authority

Harvest observes the workspace. Harvest does not interpret the work.

A dirty tree does not mean broken. A clean tree does not mean complete.
A commit does not mean a Mission finished. A branch does not mean active
development. A local tracking ref does not mean GitHub currently has that
state. A missing checkout does not mean a Clank is abandoned. An old
observation does not mean the observation was wrong.

UNKNOWN stays UNKNOWN.

## LOCAL_GIT provenance

`LOCAL_GIT_STATE_OBSERVED` is Clank-level code evidence:

- `source` is always `LOCAL_GIT` (fixed by the recorder)
- `mission_id` is null
- `session_id` is null
- `bind_session` is false
- no checkpoint, Mission, Session, handoff, reconciliation, CI artefact,
  or deployment observation is created

`LOCAL_GIT_HARVEST_COMPLETED` records that ClankOps actually walked the
fleet:

- `source` is always `SYSTEM`
- per-target facts may reference observation events
- callers cannot relabel either event as `GITHUB`, `CI`, `DEPLOYMENT`,
  or `USER` observation of code facts

`--source GITHUB harvest local-git` still writes `LOCAL_GIT` / `SYSTEM`.

Harvest never overwrites Foundation 3 `reconcile` output. Live reconcile
observation and last harvested observation are separate read-model
blocks (`reconcile` vs `local_git_harvest`).

## Semantic state vs harvest freshness

Two event kinds:

1. `LOCAL_GIT_STATE_OBSERVED` — first successful observation, or a
   semantic state change. Deduplicated by fingerprint.
2. `LOCAL_GIT_HARVEST_COMPLETED` — one per completed invocation, even
   when nothing changed.

The fingerprint excludes observation time, event id, actor, run id, and
display age. It includes checkout identity, branch/detached, HEAD,
clean/dirty, counts, local upstream ahead/behind, safe remote
identities, and worktree inventory. Registered alternate checkouts are
ClankOps registry/ref facts, not Git-observed facts, and are not part
of `LOCAL_GIT_STATE_OBSERVED` or its semantic fingerprint.

## Why identical state is deduplicated

The ledger is institutional memory, not a debug logfile. An hourly walk
of 18 unchanged repositories must not mint 18 duplicate state events.

Harvesters record the latest observation generation, inspect **without**
a SQLite write lock, stamp `observed_at` when inspection completes, then
`BEGIN IMMEDIATE` and re-read generation plus fingerprint. Identical
fingerprints dedup. `ledger_seq` detects that another writer landed; it
does not decide which Git observation is newer. If fingerprints differ
after a concurrent write, `observed_at` is compared. A strictly newer
incoming observation is appended even if it acquired the write lock
second. An older, tied, or unorderable incoming observation is discarded
as `STALE_OBSERVATION`. Each invocation still gets its own harvest-run
record.

## Harvest-run evidence

The run event carries bounded metadata: `run_id`, scope, timestamps,
counts, and per-target result records (no dirty filenames, no raw Git
stdout/stderr). That is how freshness advances when state does not.

## No-network rule

Harvest never runs `git fetch`, `pull`, `push`, `clone`, or `ls-remote`.
Ahead/behind is **against the currently available local tracking ref**.
Remote freshness is unknown. The payload states that explicitly as
`upstream_ahead_local` / `upstream_behind_local`.

There is no scheduler, daemon, SSH, or GitHub polling **inside** Harvest 1.
The command remains one-shot. [Fleet Pulse 1](FLEET_PULSE_1.md) may invoke
it on a Windows cadence; Pulse is a caller, not Harvest 2 semantics.

## Dirty-path privacy

Persist counts, not paths. `dirty`, `dirty_count`, `tracked_changes`,
and `untracked` are stored. Filenames from `git status` are not.

## Remote credential redaction

Git remotes can contain credentials. Harvest never persists or prints a
remote URL containing userinfo, tokens, passwords, or query-string
secrets. Safe identities look like `github.com/anil-ganti-nbc/oem-radar`.
If a remote cannot be safely normalised: `present=true`, `redacted=true`,
and no raw value.

Foundation 3's inspector is unchanged. Harvest reuses `run_git` and
`github_repo_id`; it does not change Foundation 3 semantics.

## Canonical checkout selection

Fleet mode inspects every registered Clank identity. Targeted mode
inspects exactly one. Arbitrary filesystem paths are not accepted.

- exactly one canonical `local_path` → inspect it
- none → `NO_CANONICAL_PATH` (skip)
- more than one canonical `local_path` → `AMBIGUOUS_CANONICAL_PATH`
  (fail closed; never guess)

Noncanonical `local_path` refs may be listed as alternate/duplicate
checkouts on harvest **results** and the Clank/ref read model. They are
not harvested by default and are not Git-observed LOCAL_GIT facts.
Changing only a noncanonical `local_path` must not emit
`LOCAL_GIT_STATE_OBSERVED`. Identities are never merged because two
paths or remotes look similar.

Without a semantic LOCAL_GIT observation, Terminal/resume source is
UNKNOWN — never a default of `LOCAL_GIT`. A successful semantic
observation uses `source=LOCAL_GIT`.

## Duplicate checkout treatment

Duplicates remain extra refs on the existing Clank. Worktrees are local
Git evidence attached to that Clank, not new Clank identities.

## Checkout identity

Checkout identity is **not** the display name. Harvest derives
`checkout_key` as `local_path:` plus a SHA-256 of the canonical
registered path after the same Windows-aware normalisation used for
workspace matching.

If the Clank's canonical `local_path` later moves, that is a **new**
checkout identity/evidence surface. Historical observations are not
silently rewritten onto the new path.

## Worktree semantics

`git worktree list --porcelain` is inventoried from the canonical
repository: path, HEAD, branch if known, detached, main worktree flag,
locked/prunable if Git reports them. Harvest 1 does not inspect each
worktree's dirty state.

## Failure isolation

One broken checkout does not abort the fleet walk. Every target is
processed, then a summary is produced. An ordinary `Exception` from
`observe_fn()` is recorded as a bounded/redacted `ERROR` for that
target; process-wide `BaseException` classes are not swallowed.

| Result | Meaning |
| --- | --- |
| `OBSERVED_CHANGED` | semantic observation appended |
| `OBSERVED_UNCHANGED` | fingerprint matched; freshness via harvest-run |
| `NO_CANONICAL_PATH` | skip (not catastrophic) |
| `AMBIGUOUS_CANONICAL_PATH` | fail closed; counted as an error |
| `PATH_MISSING` | registered path unavailable |
| `NOT_A_GIT_REPOSITORY` | path exists but is not a Git work tree |
| `TIMEOUT` | a Git invocation timed out |
| `ERROR` | other inspection failure (including missing Git binary) |
| `STALE_OBSERVATION` | concurrent older observation discarded; Git inspect succeeded |

These are harvest results. They are not Clank health.

Last known good semantic state survives a later failed check.

## Dry-run

`clankctl harvest local-git --dry-run` (and `--json`) inspects, computes
what would change, prints/returns results, and writes **zero** events
and **zero** projection rows. `ledger_seq` and the resume context
fingerprint are unchanged.

Use dry-run before the first real fleet dogfood.

## Resume fingerprint behaviour

The resume packet exposes `local_git_harvest`. Identical successful
semantic state must not churn `context_fingerprint` even when
`last_checked_at`, age, run id, or latest result (`OBSERVED_UNCHANGED`)
change. A real HEAD, branch, dirty, count, remote-identity, or worktree
change must.

A new failure/unavailability outcome is evidence. Successful state X
then `TIMEOUT` / `ERROR` / `PATH_MISSING` / `NOT_A_GIT_REPOSITORY`
changes `context_fingerprint`. Repeated identical failure class with no
other semantic change need not churn. Failure class is hashed as
`latest_failure`; timestamps and successful unchanged freshness stay
ephemeral.

## Non-conflation laws

| Observed | Does not mean |
| --- | --- |
| local HEAD exists | Mission complete |
| clean tree | handoff |
| dirty tree | blocked Mission |
| branch `main` | production deployment |
| local upstream ahead=0 | GitHub confirmed aligned |
| checkout missing | Clank abandoned |
| harvest failed | repository broken |
| old harvest | false evidence |
| worktree exists | separate Clank identity |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | harvest invocation completed; no hard target errors |
| 1 | harvest completed, but one or more targets had hard errors/timeouts (`TIMEOUT`, `ERROR`, `AMBIGUOUS_CANONICAL_PATH`, `STALE_OBSERVATION`) |
| 2 | invocation/validation/control-plane failure (unknown Clank, usage) |

`NO_CANONICAL_PATH` is reported as a skip and does not by itself force
exit 1. `PATH_MISSING` / `NOT_A_GIT_REPOSITORY` are unavailable, not
hard errors.

## Commands

```text
clankctl harvest local-git
clankctl harvest local-git <clank>
clankctl --json harvest local-git
clankctl --json harvest local-git <clank>
clankctl harvest local-git --dry-run
```

Subprocess contract: argv list, `shell=False`, `GIT_TERMINAL_PROMPT=0`,
`GIT_OPTIONAL_LOCKS=0`, hooks disabled via `core.hooksPath=`, bounded
timeouts, bounded redacted stderr if needed.

## Attention

Harvest 1 does **not** add `LOCAL_GIT_NEVER_HARVESTED` or
`LOCAL_GIT_EVIDENCE_STALE`. Those would fire for every canonical
checkout that has never been harvested and would change Foundation 7
reason membership. The harvested read model is mandatory; new attention
reasons are deferred.

## Deferred to later Harvest tranches

- GitHub polling and remote freshness
- `git fetch` / pull
- automatic branch cleanup, stash, commit, checkout repair
- automatic Mission/Session/handoff/reconciliation changes
- deployment inspection, CI capture
- remote host / NAS harvesting
- `LOCAL_GIT_NEVER_HARVESTED` / `LOCAL_GIT_EVIDENCE_STALE` attention
  reasons
- semantic/vector search, dependency graph, historical reconstruction
- Standards / Motherclank / Quartermaster authority
