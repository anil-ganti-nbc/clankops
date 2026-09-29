"""Build the COPS-000080 manifest from the local frozen contract.

The phase is an explicit operator observation, never inferred from a chat or
Git branch. This script writes no ClankOps events.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

EXPECTED_CONTRACT = "a7c2f1d10d0bf427642c437362446165bb519adf7c05a95f7a980d0558f53456"
PHASES = ["INTAKE", "RECON", "SPEC", "BUILD", "TEST", "REVIEW", "ACCEPTANCE",
          "DEPLOY", "SOAK", "PROMOTE", "CLOSED", "REWORK", "BLOCKED",
          "HUMAN_REQUIRED", "ROLLED_BACK", "FAILED"]
TERMINAL_RESULTS = ["PASS", "ROLLED_BACK", "HUMAN_REQUIRED", "FAILED"]
START_PHASES = ["INTAKE", "RECON", "SPEC", "BUILD", "TEST", "REVIEW",
                "ACCEPTANCE", "DEPLOY", "SOAK", "PROMOTE", "REWORK", "BLOCKED"]
GATE_KEYS = [
    "contract_frozen_before_build", "clankops_mission_and_sessions",
    "cnx_only_in_discovery_provenance", "qualified_candidates_have_primary_domain_and_board_surface",
    "active_and_placeholder_dedup", "focused_tests", "applicable_full_suite",
    "independent_sol_review", "open_review_findings", "reviewed_sha_equals_candidate_sha",
    "operational_source_registry_diff", "operational_db_event_outbox_diff",
    "replay_new_candidates", "real_cnx_run_with_fetch_provenance",
    "observation_only_deployment", "natural_cycles", "rollback_drill",
]


def build(contract: bytes, phase: str) -> dict:
    digest = hashlib.sha256(contract).hexdigest()
    if digest != EXPECTED_CONTRACT:
        raise ValueError("contract-v1.md hash differs from the frozen COPS-000080 contract")
    if phase not in START_PHASES:
        raise ValueError("start phase must be an observed nonterminal phase")
    gates = [{"key": key, "scope": "contract" if index < 2 else "candidate",
              "description": key.replace("_", " ")} for index, key in enumerate(GATE_KEYS)]
    edges = [("INTAKE", "RECON"), ("RECON", "SPEC"), ("SPEC", "BUILD"),
             ("BUILD", "TEST"), ("TEST", "REVIEW"), ("REVIEW", "REWORK"),
             ("ACCEPTANCE", "REWORK"), ("REWORK", "SPEC"), ("REWORK", "BUILD"),
             ("REVIEW", "ACCEPTANCE"), ("ACCEPTANCE", "DEPLOY"),
             ("DEPLOY", "SOAK"), ("SOAK", "PROMOTE"), ("PROMOTE", "CLOSED"),
             ("BLOCKED", "REWORK")]
    edges += [(src, dst) for src in START_PHASES if src != "BLOCKED"
              for dst in ("BLOCKED", "HUMAN_REQUIRED", "ROLLED_BACK", "FAILED")]
    transitions = [{"from": src, "to": dst,
                    "requires": (["focused_tests", "applicable_full_suite"] if (src, dst) == ("TEST", "REVIEW")
                                 else ["independent_sol_review", "open_review_findings",
                                       "reviewed_sha_equals_candidate_sha"]
                                 if (src, dst) == ("REVIEW", "ACCEPTANCE")
                                 else ["real_cnx_run_with_fetch_provenance", "replay_new_candidates",
                                       "operational_source_registry_diff", "operational_db_event_outbox_diff"]
                                 if (src, dst) == ("ACCEPTANCE", "DEPLOY")
                                 else ["observation_only_deployment"]
                                 if (src, dst) == ("DEPLOY", "SOAK")
                                 else ["natural_cycles", "rollback_drill"]
                                 if (src, dst) == ("SOAK", "PROMOTE") else [])}
                   for src, dst in edges]
    return {"schema_version": 1, "mission": "COPS-000080", "contract_sha256": digest,
            "start_phase": phase, "phases": PHASES, "terminal_results": TERMINAL_RESULTS,
            "gates": gates, "transitions": transitions}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--phase", choices=START_PHASES, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.contract.read_bytes(), args.phase)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out} for COPS-000080 at {args.phase}; contract sha256={EXPECTED_CONTRACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
