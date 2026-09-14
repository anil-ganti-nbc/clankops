# Fleet Pulse 1 — scheduled local harvest

Fleet Harvest 1 gave ClankOps eyes. Terminal Beta gave the operator a
cockpit. Fleet Pulse 1 makes the eyes blink automatically. It does not
give ClankOps hands.

## Purpose

Keep recorded local-Git evidence fresh on the development machine
without typing `harvest local-git` all day.

Pulse schedules observation. Pulse does not interpret the work. It does
not decide what Mission is active, whether dirty is bad, whether clean
means complete, whether a branch is important, whether a commit should
be pushed, whether GitHub is current, whether a Session should close,
whether a handoff happened, or whether anything should deploy.

It only causes the existing one-shot harvest observer to run:

```text
<resolved-python.exe> … --db <resolved-db> harvest local-git
```

## Architecture

Preferred architecture on the current primary operator machine:

```text
Windows Task Scheduler
    ->
one-shot existing command
    ->
clankctl harvest local-git
```

There is no long-lived Python daemon, no hidden HTTP server, no
browser launch, and no generic cross-platform scheduler abstraction.

Python owns the deterministic task spec (`clankops.pulse` /
`clankctl pulse spec`). The thin installer is
`scripts/clankops-harvest-task.ps1`. Importing the package, installing
the Python distribution, starting Terminal, or running harvest does
**not** register a task.

Canonical task name: `ClankOps Fleet Harvest`.

## Default cadence

Default production cadence: **every 10 minutes**.

Accepted interval: integer **1–1440** minutes. Sub-minute scheduling is
rejected. This is near-live workspace evidence, not filesystem tracing.

Cadence is chosen at install time (`-IntervalMinutes`).

## Explicit install only

Installing a scheduled task mutates the host OS. Therefore:

```powershell
.\scripts\clankops-harvest-task.ps1 dry-run
.\scripts\clankops-harvest-task.ps1 install
.\scripts\clankops-harvest-task.ps1 status
.\scripts\clankops-harvest-task.ps1 remove
```

Normal ClankOps install must not create the task. Only the explicit
installer operation may.

Foundation 1 still forbids wrapping **collection** launchers in Task
Scheduler. Fleet Pulse 1 is an opt-in ClankOps observer cadence, not
collection automation.

## One-shot only

Each scheduled invocation is one process running one fleet harvest. It
exits when that harvest completes. No resident process. No infinite
loop.

## Resolved Python and database

The installer resolves, then persists, the exact interpreter and
database path. The task does not depend on the current working
directory, an interactive shell profile, `PATH` containing `Scripts`,
or `CLANKOPS_DB` magically existing in the Task Scheduler environment.

Database resolution matches ClankOps conventions:

1. `CLANKOPS_DB` if set
2. otherwise `%USERPROFILE%\.clankops\clankops.db`

The scheduled action stores that absolute path as `--db`. The Python
executable is the interpreter that can import this ClankOps checkout,
not the bare word `python`.

## Dry-run

`dry-run` makes **zero** Task Scheduler changes. It prints:

- task name
- cadence
- resolved Python executable
- resolved database
- overlap policy
- command argv / argument string

No secrets. Secret-shaped values are refused.

## Install idempotence

One canonical task identity. Installing twice with the same
configuration is success without a duplicate. A different configuration
replaces the existing task and reports the changed fields.

## Remove

Remove deletes only `ClankOps Fleet Harvest`. It does not delete ledger
evidence, the ClankOps database, logs, Missions, unrelated Python
processes, or other scheduled tasks. If no task exists, the result is
deterministic and benign.

## Status

`status` reports scheduler facts only: installed, task name, cadence,
next run if Windows exposes it, last run if Windows exposes it, last
task result if available, and configured command identity.

This is not health, fleet health, or harvest health. Task Scheduler
result codes are not Clank correctness.

## Overlap

If a harvest is still running when the next trigger fires, Windows is
configured **IgnoreNew**: do not start a second instance.

## Missed runs

`StartWhenAvailable = true` means one harvest may run after a missed
trigger. Missed 10-minute intervals are not replayed. One current
observation is sufficient. The machine is not woken solely to harvest.
If the machine is asleep or off, missed observations are simply missed.

## Power / laptop

Harvest is lightweight local Git inspection. The task is allowed on
battery. "AC power only" is not a hidden requirement. No stored Windows
account password. Current-user, run when logged on, Limited run level.

## Failure semantics

Scheduled harvest preserves Harvest 1 exit meanings:

| Exit | Meaning |
| --- | --- |
| 0 | harvest completed with no hard target errors |
| 1 | harvest completed, one or more targets had hard harvest errors |
| 2 | invocation / control-plane failure |

The scheduler must not reinterpret these as Clank broken, Mission
failed, or fleet unhealthy. A later trigger still runs after exit 1 or
2. ClankOps does not write a new ledger event for the scheduler's
process status.

## No network

Scheduled harvest inherits Fleet Harvest 1's no-network law. Forbidden:
`git fetch` / `pull` / `push` / `clone` / `ls-remote`, GitHub API
polling, SSH, NAS inspection, deployment inspection, CI capture.
Ahead/behind remains based on currently available local tracking refs.

## No authority changes

Scheduled harvest never creates, resumes, pauses, completes, or
reconciles a Mission; never opens or closes a Session; never records a
handoff; never captures CI or deployment; never alters Standards,
Motherclank, or Quartermaster state.

## No Terminal mutation

Terminal remains read-only, snapshot-default, GET/HEAD. The browser
never initiates harvest. There is no "Harvest now" button, scheduler
toggle, POST route, or hidden background harvest. The scheduled task
updates the database independently. Terminal sees fresher recorded
evidence on its next snapshot refresh.

## Event volume

Fleet Harvest intentionally emits one `LOCAL_GIT_HARVEST_COMPLETED` per
invocation even when semantic state is unchanged. At a 10-minute
cadence that is **144 run events/day**. That is accepted. Run freshness
is evidence. Do not optimise those events away.

Semantic `LOCAL_GIT_STATE_OBSERVED` events remain deduplicated exactly
as Harvest 1 defined.

Pulse does not add `LOCAL_GIT_NEVER_HARVESTED` or
`LOCAL_GIT_EVIDENCE_STALE`. Terminal already exposes harvest freshness.

## Security / privacy

The task action contains only operational values required to run
ClankOps: Python path, database path, harvest command, cadence/task
metadata. It never contains tokens, passwords, webhook URLs, Git
credentials, or arbitrary inherited environment strings.

Windows paths with spaces are passed through ScheduledTask action
fields (execute vs argument vector), not concatenated through `cmd.exe`.
The installer uses ScheduledTasks cmdlets, not `Invoke-Expression`.

## Troubleshooting

- `dry-run` first. If the printed Python/DB/cadence is wrong, do not
  install.
- `status` after install. If `installed: no`, the explicit installer
  has not been run (or the task was removed).
- Harvest exit 1 is a target error from Harvest 1, not a Pulse failure.
- If harvest evidence is stale, run one-shot `clankctl harvest local-git`
  manually. Do not assume missed sleeps were observed.
- Remove never deletes `clankops.db`.

## CLI (spec only)

These commands do **not** register a Windows task:

```powershell
python -m clankops --json pulse spec
python -m clankops --json pulse plan
python -m clankops --json pulse remove-plan
```
