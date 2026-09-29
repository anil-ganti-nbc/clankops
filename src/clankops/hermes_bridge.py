"""One-shot Hermes reviewer with a ClankOps Mission and exact checkout binding."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from clankops.enums import EventType
from clankops.errors import InvalidTransitionError, ValidationError
from clankops.factory import record_review, status
from clankops.gitinspect import inspect_git
from clankops.redact import sanitize_text
from clankops.store import Store

_SHA = re.compile(r"^[0-9a-f]{40}$")
_VERDICT = re.compile(r"^\s*(PASS|REQUEST_CHANGES|HUMAN_REQUIRED|FAILED)(?:\s|:|$)")
_REVIEW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


def review(
    store: Store,
    mission: str,
    *,
    candidate_sha: str,
    review_id: str,
    checkout: Path,
    prompt_file: Path,
    contract_file: Path,
    hermes: str = "hermes",
    model: str = "gpt-5.6-sol",
    provider: str = "openai-codex",
    timeout: int = 1200,
    actor: str | None = None,
) -> dict[str, Any]:
    """Return the model's report, recording only bounded metadata in the ledger.

    Exit zero and matching usage prove a completed call to the selected model,
    not the accuracy of its verdict. A review never marks an acceptance gate PASS.
    """
    if not _SHA.fullmatch(candidate_sha) or timeout < 1 or timeout > 3600 or not _REVIEW_ID.fullmatch(review_id):
        raise ValidationError("invalid candidate SHA, review id, or Hermes timeout")
    if not _MODEL_ID.fullmatch(model) or not _MODEL_ID.fullmatch(provider):
        raise ValidationError("invalid Hermes model or provider identifier")
    state = status(store, mission)
    if state["phase"] != "REVIEW" or state["candidate_sha"] != candidate_sha:
        raise InvalidTransitionError("Hermes review requires the exact REVIEW candidate")
    try:
        contract_digest = hashlib.sha256(contract_file.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValidationError("frozen contract file cannot be read") from exc
    if contract_digest != state["contract_sha256"]:
        raise ValidationError("frozen contract file has changed")
    # A review ID belongs to the whole Mission, not just its latest projection.
    # Otherwise an older ID can invoke Hermes again after a newer review.
    rows = store.conn.execute(
        "SELECT payload_json FROM events WHERE mission_id = ? AND event_type = ? "
        "ORDER BY ledger_seq DESC",
        (state["mission_id"], EventType.FACTORY_HERMES_REVIEW_RECORDED),
    )
    for row in rows:
        previous = json.loads(row[0])["state"]["last_review"]
        if previous.get("review_id") == review_id:
            if previous.get("candidate_sha") != candidate_sha:
                raise ValidationError("review id already belongs to another candidate")
            return {"review": previous, "response": "", "replayed": True}
    checkout = checkout.resolve()
    if not checkout.is_dir():
        raise ValidationError("review checkout does not exist")
    git = inspect_git(checkout)
    if git.get("head") != candidate_sha or git.get("dirty") is not False:
        raise ValidationError("review checkout must be clean and at the candidate SHA")
    try:
        task = prompt_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("review prompt file cannot be read") from exc
    if not task.strip() or len(task) > 20_000:
        raise ValidationError("review prompt must be nonempty and at most 20,000 characters")
    preamble = (
        "Independent review of only the evidence supplied in this prompt. "
        "No repository or other tools are available. If the diff, tests, or acceptance "
        "evidence are insufficient, report HUMAN_REQUIRED rather than PASS.\n"
        f"Mission: {state['mission']}\nContract sha256: {state['contract_sha256']}\n"
        f"Frozen manifest sha256: {state['manifest_sha256']}\n"
        f"Exact candidate commit: {candidate_sha}\n"
        "Report PASS, REQUEST_CHANGES, HUMAN_REQUIRED, or FAILED with specific evidence. "
        "Your response is an agent report and cannot itself pass an acceptance gate.\n\n"
    )
    with tempfile.TemporaryDirectory(prefix="clankops-hermes-") as temp:
        usage_path = Path(temp) / "usage.json"
        try:
            proc = subprocess.run(
                [hermes, "--safe-mode", "-t", "bot_room", "-z", preamble + task,
                 "-m", model, "--provider", provider,
                 "--usage-file", str(usage_path)],
                cwd=checkout, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, shell=False,
            )
            code: int | None = proc.returncode
            output = proc.stdout or ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            code = None
            output = ""
            failure = "TIMEOUT" if isinstance(exc, subprocess.TimeoutExpired) else "START_FAILED"
        else:
            failure = None
        try:
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            usage = {}
    after = inspect_git(checkout)
    unchanged = after.get("head") == candidate_sha and after.get("dirty") is False
    confirmed = (code == 0 and isinstance(usage, dict) and
                 usage.get("model") == model and usage.get("provider") == provider and
                 usage.get("completed") is True and not usage.get("failed") and
                 not usage.get("partial") and not usage.get("interrupted") and unchanged)
    match = _VERDICT.search(output[:1000]) if confirmed else None
    verdict = match.group(1) if match else "UNVERIFIED"
    report = {
        "review_id": review_id, "candidate_sha": candidate_sha,
        "model_requested": model, "provider_requested": provider,
        "model_observed": usage.get("model") if isinstance(usage, dict) and
                          isinstance(usage.get("model"), str) and _MODEL_ID.fullmatch(usage["model"]) else None,
        "provider_observed": usage.get("provider") if isinstance(usage, dict) and
                             isinstance(usage.get("provider"), str) and _MODEL_ID.fullmatch(usage["provider"]) else None,
        "exit_code": code, "completed_call": bool(confirmed), "reported_verdict": verdict,
        "response_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest() if output else None,
        "checkout_unchanged": unchanged, "failure": failure,
    }
    record_review(store, mission, report, actor=actor)
    return {"review": report, "response": (sanitize_text(output[:8000]) or ""),
            "replayed": False}
