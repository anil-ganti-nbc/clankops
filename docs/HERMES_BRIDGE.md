# Hermes ↔ ClankOps factory bridge

This is a local, synchronous bridge for a Grok Bot coordinator on Windows. ClankOps owns the Mission, frozen contract, phases, gate results, and event ledger. Hermes supplies a one-shot independent Sol review. The response is recorded as `AGENT_REPORT` metadata; it never marks a gate PASS. No gateway configuration, Hermes cron job, Kanban task, source admission, or production deployment is created by these commands.

## COPS-000080 setup

Use the existing Board Clank Mission. Do not create another Mission or change COPS-000074. Install this ClankOps checkout with Python 3.14+, then obtain the latest COPS-000080 checkpoint on the Windows PC. The phase supplied to the manifest generator must be the phase actually recorded there. The frozen contract file is `C:\Users\anil\Clanks\_Factory\cnx-sbc-oem-discovery\contract-v1.md`; the generator refuses any hash other than `a7c2f1d10d0bf427642c437362446165bb519adf7c05a95f7a980d0558f53456`.

PowerShell example, from the ClankOps checkout:

```powershell
$contract = 'C:\Users\anil\Clanks\_Factory\cnx-sbc-oem-discovery\contract-v1.md'
$manifest = 'C:\Users\anil\Clanks\_Factory\cnx-sbc-oem-discovery\factory-manifest.json'
py -3.14 examples\factory\make_cnx_manifest.py --contract $contract --phase REWORK --out $manifest
py -3.14 -m clankops --actor grok --json factory freeze COPS-000080 --manifest $manifest --contract $contract
py -3.14 -m clankops --actor grok --json factory status COPS-000080
```

Replace `REWORK` with the observed phase. The generator includes the contract's normal phases, exception states, four terminal results, and all 17 frozen CNX acceptance keys. `PASS` is a terminal result recorded when the Mission reaches `CLOSED`, not a phase. `factory freeze` is one-time and rejects a second contract. If ClankOps is already frozen, use `factory status`; do not replace the manifest or rewrite history. The bridge starts recording phase changes from installation onward. It does not claim that earlier manual checkpoints were mechanically enforced.

## Candidate and Sol review

The candidate checkout must be clean and its HEAD must equal the exact SHA. The prompt file must contain the relevant diff, test results, and actual acceptance evidence, because the review process has no tools with which to inspect local files. A first line of the model's reply must begin `PASS:`, `REQUEST_CHANGES:`, `HUMAN_REQUIRED:`, or `FAILED:`. If the bounded prompt cannot contain enough evidence, record `HUMAN_REQUIRED` and use another review route. Run the review only in the `REVIEW` phase:

```powershell
$sha = '<40-character-reviewed-candidate-SHA>'
$wt = 'C:\Users\anil\Clanks\_Factory\cnx-sbc-oem-discovery\wt-reviewed'
$prompt = 'C:\Users\anil\Clanks\_Factory\cnx-sbc-oem-discovery\sol-review-prompt.txt'
py -3.14 -m clankops --actor grok --json factory candidate COPS-000080 $sha
py -3.14 -m clankops --actor grok --json factory hermes-review COPS-000080 --candidate-sha $sha --review-id "COPS-000080-sol-$sha-round4" --checkout $wt --contract $contract --prompt-file $prompt --model gpt-5.6-sol --provider openai-codex
```

Candidate selection may happen in BUILD, REWORK, TEST, or REVIEW. Replacing a reviewed candidate requires the frozen `REVIEW → REWORK` transition first. Selection invalidates candidate-scoped gate results and the prior review. A sequential repeated `--review-id` returns its recorded result without invoking Hermes again; do not launch concurrent reviews with the same ID. The bridge invokes `hermes --safe-mode -t bot_room -z` with `--usage-file` synchronously. The installed `bot_room` toolset is empty; confirm that on Hermes updates. It checks Hermes's exit status and usage report for the requested model/provider and a completed run, then checks the checkout again. A failed, mismatched, interrupted, or unconfirmed call is recorded as unverified and the command exits nonzero. Up to 8,000 characters of the model reply are returned to the caller; the ledger retains only the full response hash, model identity, status, and reported verdict. Hermes still writes its own local session data and sends the supplied prompt to its configured inference provider.

Hermes's [CLI documentation](https://hermes-agent.nousresearch.com/docs/reference/cli-commands/) describes `-z`, model/provider overrides, and `--usage-file`; verify those flags on the installed Windows version before the pilot. If its usage report differs, leave the review unverified and adapt the bridge after observing the local CLI.

## Gates and phase changes

For every acceptance gate, the coordinator supplies an evidence reference and a declared basis. A Hermes report alone does not update a gate, and `AGENT_REPORT` is rejected as a gate basis. The bridge binds attestations to the candidate, but it does **not** fetch or independently verify the referenced CI, GitHub, deployment, or local evidence. An agent can enter a claimed basis through the CLI. The event's source still identifies the recorder. Before advancing to ACCEPTANCE or CLOSED, the operator or local Codex must verify each reference against its actual source; a `USER` basis is a human attestation. Gate results remain tied to the selected candidate SHA except the first two contract-scoped CNX gates.

```powershell
py -3.14 -m clankops --actor grok --json factory gate COPS-000080 focused_tests PASS --evidence-ref 'local:test-log:sha256:<digest>' --basis LOCAL_GIT
py -3.14 -m clankops --actor grok --json factory advance COPS-000080 --to REVIEW --expect-phase TEST --expect-candidate $sha
```

The transition graph is frozen in the manifest. `REVIEW → ACCEPTANCE` requires a completed Hermes PASS report for the current SHA and the review gates. `PROMOTE → CLOSED` requires all 17 gates to be PASS for that SHA or the frozen contract, as appropriate, and records terminal result `PASS`. Direct `mission complete`, `mission reconcile --to COMPLETED`, and `handoff --state COMPLETED` are refused on a factory managed Mission before that result. They do not change existing Missions without a factory contract.

The CNX deploy, two natural cycles, and rollback drill remain separate gates. This bridge cannot prove those facts merely from a model answer or a local test run. It also does not itself start a scheduler or touch the Board operational database.
