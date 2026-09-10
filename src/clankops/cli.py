"""clankctl — Foundation 0 command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from clankops.brief import format_brief, format_history
from clankops.census import default_census_path, load_census, run_census, write_census
from clankops.db import current_schema_version
from clankops.context import (
    RECOVERY_HINT,
    build_context,
    env_from_os,
    format_powershell_env,
    is_clankops_tool_checkout,
    read_clank_context,
    validate_session_env,
    write_context,
)
from clankops.enums import EventSource, FeatureState, MissionState
from clankops.errors import ClankOpsError, NotFoundError, ValidationError
from clankops.gitinspect import inspect_git
from clankops.store import Store, open_readonly_store, open_store

DEFAULT_DB = Path(os.environ.get("CLANKOPS_DB") or (Path.home() / ".clankops" / "clankops.db"))
USER_ACTORS = frozenset({"user", "operator"})


def resolve_invocation_source(actor: str | None, source: str | None) -> str:
    """Default provenance: USER for human actors, AGENT_REPORT otherwise.

    An explicit --source always wins. Capturing git never changes this.
    """
    if source:
        return str(source)
    if (actor or "").strip().lower() in USER_ACTORS:
        return EventSource.USER
    return EventSource.AGENT_REPORT


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n"


def _print(text: str, *, as_json: bool, payload: Any | None = None) -> None:
    if as_json and payload is not None:
        sys.stdout.write(_json(payload))
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _store(args: argparse.Namespace) -> Store:
    session = getattr(args, "session", None) or os.environ.get("CLANKOPS_SESSION_ID") or None
    if session:
        session = session.strip() or None
    return open_store(args.db, actor=args.actor, source=args.source, session_id=session)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _capture_git(store: Store, mission_token: str) -> dict[str, str | None]:
    mission = store.resolve_mission(mission_token)
    path = store.canonical_local_path(mission["clank_id"])
    if not path or not Path(path).exists():
        return {}
    git = inspect_git(path)
    return {
        "branch": git.get("current_branch"),
        "head": git.get("head"),
        "working_tree": _git_working_label(git),
    }


def _git_evidence(store: Store, mission_token: str) -> dict[str, Any] | None:
    fields = _capture_git(store, mission_token)
    if not any(fields.values()):
        return None
    return {"source": EventSource.LOCAL_GIT, **fields}


def cmd_init(args: argparse.Namespace) -> int:
    store = _store(args)
    version = current_schema_version(store.conn)
    _print(
        f"database ready: {args.db} (schema {version})",
        as_json=args.json,
        payload={"db": str(args.db), "schema_version": version},
    )
    store.conn.close()
    return 0


def cmd_census(args: argparse.Namespace) -> int:
    roots = args.roots or None
    census = run_census(roots=roots, include_github=not args.no_github)
    out_path = Path(args.out) if args.out else None
    if out_path:
        write_census(census, out_path)
    if args.do_import:
        store = _store(args)
        stats = store.import_census(census, actor=args.actor, source=EventSource.RECONSTRUCTED)
        store.conn.close()
        census["import_stats"] = stats
        if out_path:
            write_census(census, out_path)
    counts = census.get("counts") or {}
    summary = [
        f"candidates: {counts.get('total_candidates', len(census.get('candidates') or []))}",
        f"VERIFIED: {counts.get('VERIFIED', 0)}",
        f"PROBABLE: {counts.get('PROBABLE', 0)}",
        f"UNKNOWN: {counts.get('UNKNOWN', 0)}",
        f"SUPPORT_COMPONENT: {counts.get('SUPPORT_COMPONENT', 0)}",
        f"NOT_A_CLANK: {counts.get('NOT_A_CLANK', 0)}",
        f"NEEDS_RECONSTRUCTION: {counts.get('NEEDS_RECONSTRUCTION', 0)}",
        f"dirty: {counts.get('dirty_repositories', 0)}",
        f"github-backed: {counts.get('github_backed', 0)}",
        f"local-only: {counts.get('local_only', 0)}",
    ]
    if out_path:
        summary.append(f"wrote: {out_path}")
    if args.do_import:
        summary.append(f"imported: {census.get('import_stats')}")
    _print("\n".join(summary), as_json=args.json, payload=census if args.json else None)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = _store(args)
    rows = store.list_clanks()
    if args.json:
        _print("", as_json=True, payload=rows)
    else:
        if not rows:
            sys.stdout.write("No Clanks registered.\n")
        for row in rows:
            sys.stdout.write(
                f"{row['slug']:32} {row['lifecycle']:14} {row.get('classification') or '-':22} "
                f"{row['clank_id']}\n"
            )
    store.conn.close()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = _store(args)
    detail = store.clank_detail(args.clank)
    if args.json:
        _print("", as_json=True, payload=detail)
    else:
        sys.stdout.write(f"{detail['display_name']} ({detail['slug']})\n")
        sys.stdout.write(f"id: {detail['clank_id']}\n")
        sys.stdout.write(f"lifecycle: {detail['lifecycle']}\n")
        sys.stdout.write(f"classification: {detail.get('classification')}\n")
        sys.stdout.write(f"description: {detail.get('description') or '-'}\n")
        sys.stdout.write(f"aliases: {', '.join(detail['aliases']) or '-'}\n")
        for ref in detail["refs"]:
            flag = "*" if ref["is_canonical"] else " "
            sys.stdout.write(f"ref{flag} {ref['ref_kind']}: {ref['ref_value']}\n")
        sys.stdout.write(f"missions: {len(detail['missions'])}\n")
        for m in detail["missions"][:10]:
            sys.stdout.write(f"  {m['display_id']} [{m['state']}] {m['objective']}\n")
    store.conn.close()
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    store = _store(args)
    row = store.register_clank(
        args.clank,
        display_name=args.name,
        description=args.description,
        aliases=args.alias or [],
        local_path=args.path,
        remotes=[args.remote] if args.remote else [],
        lifecycle=args.lifecycle or "UNKNOWN",
        actor=args.actor,
        source=args.source,
    )
    _print(
        f"registered {row['slug']} {row['clank_id']}",
        as_json=args.json,
        payload=row,
    )
    store.conn.close()
    return 0


def cmd_mission_start(args: argparse.Namespace) -> int:
    store = _store(args)
    row = store.start_mission(args.clank, args.objective, actor=args.actor, source=args.source)
    session = row.get("session_id")
    msg = f"started {row['display_id']} [{row['state']}] {row['objective']}"
    if session:
        msg += f" session={session}"
    _print(msg, as_json=args.json, payload=row)
    store.conn.close()
    return 0


def cmd_mission_list(args: argparse.Namespace) -> int:
    store = _store(args)
    rows = store.list_missions(args.clank)
    if args.json:
        _print("", as_json=True, payload=rows)
    else:
        if not rows:
            sys.stdout.write("No missions.\n")
        for row in rows:
            sys.stdout.write(f"{row['display_id']} [{row['state']}] {row['objective']}\n")
    store.conn.close()
    return 0


def cmd_mission_transition(args: argparse.Namespace) -> int:
    store = _store(args)
    action = args.mission_action
    if action == "pause":
        row = store.pause_mission(args.mission, actor=args.actor, source=args.source)
    elif action == "resume":
        row = store.resume_mission(args.mission, actor=args.actor, source=args.source)
    elif action == "complete":
        row = store.complete_mission(args.mission, actor=args.actor, source=args.source)
    elif action == "abandon":
        row = store.abandon_mission(args.mission, actor=args.actor, source=args.source)
    elif action == "block":
        row = store.block_mission(args.mission, actor=args.actor, source=args.source)
    else:
        raise ClankOpsError(f"unknown mission action {action}")
    extra = f" session={row['session_id']}" if row.get("session_id") else ""
    _print(
        f"{row['display_id']} -> {row['state']}{extra}",
        as_json=args.json,
        payload=row,
    )
    store.conn.close()
    return 0


def _git_working_label(git: dict[str, Any]) -> str | None:
    if git.get("dirty") is True:
        return f"dirty ({git.get('dirty_count')} paths)"
    if git.get("dirty") is False:
        return "clean"
    return None


def _persist_work_context(
    store: Store,
    args: argparse.Namespace,
    mission: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    clank = store.clank_detail(mission["clank_id"])
    path = store.canonical_local_path(clank["clank_id"])
    git = inspect_git(path) if path and Path(path).exists() else {}
    brief = format_brief(store.brief(clank["slug"]))
    payload = build_context(
        db=args.db,
        actor=args.actor,
        clank={**clank, "local_path": path},
        mission=mission,
        session=session,
        git=git,
        brief=brief,
    )
    write_context(payload)
    return payload


def _resolve_work_mission(store: Store, token: str) -> dict[str, Any]:
    try:
        return store.resolve_mission(token)
    except NotFoundError:
        pass
    unfinished = store.unfinished_missions(token)
    if not unfinished:
        raise ValidationError(
            f"no unfinished Mission for {token}. "
            f"Read the brief, then: python -m clankops --actor cursor work start {token} \"<objective>\""
        )
    if len(unfinished) > 1:
        listed = ", ".join(f"{m['display_id']} [{m['state']}]" for m in unfinished)
        raise ValidationError(
            f"multiple unfinished Missions for {token}: {listed}. "
            "Pass an explicit Mission id to work resume."
        )
    return unfinished[0]


def cmd_work_start(args: argparse.Namespace) -> int:
    store = _store(args)
    unfinished = store.unfinished_missions(args.clank)
    if unfinished and not args.new_mission:
        listed = ", ".join(f"{m['display_id']} [{m['state']}]" for m in unfinished)
        raise ValidationError(
            f"unfinished Mission already exists ({listed}). "
            "Resume it with: python -m clankops --actor cursor work resume "
            f"{unfinished[0]['display_id']}  (pass --new-mission only for a new objective)"
        )
    row = store.start_mission(args.clank, args.objective, actor=args.actor, source=args.source)
    session = store.resolve_session(row["session_id"])
    payload = _persist_work_context(store, args, store.resolve_mission(row["mission_id"]), session)
    extra = f" session={session['session_id']}"
    _print(
        f"started {row['display_id']} [{row['state']}] {row['objective']}{extra}\n"
        + format_powershell_env(payload),
        as_json=args.json,
        payload=payload,
    )
    store.conn.close()
    return 0


def cmd_work_resume(args: argparse.Namespace) -> int:
    store = _store(args)
    mission = _resolve_work_mission(store, args.target)
    opened = store.open_work_session(mission["mission_id"], actor=args.actor, source=args.source)
    payload = _persist_work_context(store, args, opened["mission"], opened["session"])
    _print(
        f"resumed {opened['mission']['display_id']} [{opened['mission']['state']}] "
        f"session={opened['session']['session_id']}\n"
        + format_powershell_env(payload),
        as_json=args.json,
        payload=payload,
    )
    store.conn.close()
    return 0


def _workspace_clank_matches(store: Store, workspace: Path) -> list[dict[str, Any]]:
    matches = store.find_clanks_for_path(workspace)
    if is_clankops_tool_checkout(workspace):
        return [m for m in matches if m.get("slug") != "clankops"]
    return matches


def _resolve_expected_clank(
    store: Store,
    *,
    explicit: str | None,
    workspace: Path,
    exported_clank_id: str | None,
) -> dict[str, Any]:
    path_matches = _workspace_clank_matches(store, workspace)
    if len(path_matches) > 1:
        listed = ", ".join(m["slug"] for m in path_matches)
        raise ValidationError(
            f"workspace {workspace} matches multiple Clanks ({listed}). "
            "Pass an explicit Clank slug to work env."
        )
    path_clank = path_matches[0] if path_matches else None

    if explicit:
        expected = store.resolve_clank(explicit)
        if path_clank and path_clank["clank_id"] != expected["clank_id"]:
            raise ValidationError(
                f"requested Clank {expected['slug']} does not match workspace "
                f"{workspace} ({path_clank['slug']}). " + RECOVERY_HINT
            )
        if exported_clank_id:
            exported = store.resolve_clank(exported_clank_id)
            if exported["clank_id"] != expected["clank_id"]:
                raise ValidationError(
                    f"exported CLANKOPS_CLANK_ID is {exported['slug']} but "
                    f"requested Clank is {expected['slug']}. " + RECOVERY_HINT
                )
        return expected

    if path_clank and exported_clank_id:
        exported = store.resolve_clank(exported_clank_id)
        if exported["clank_id"] != path_clank["clank_id"]:
            raise ValidationError(
                f"exported identity is {exported['slug']} but this workspace is "
                f"{path_clank['slug']}. " + RECOVERY_HINT
            )
        return path_clank

    if path_clank:
        return path_clank

    if exported_clank_id:
        return store.resolve_clank(exported_clank_id)

    raise ValidationError(
        "cannot determine expected Clank for work env. Pass a Clank slug, "
        "--path of a registered checkout, or export CLANKOPS_CLANK_ID from "
        "work resume. last-active.json is not proof of workspace identity. "
        + RECOVERY_HINT
    )


def cmd_work_env(args: argparse.Namespace) -> int:
    store = _store(args)
    env = env_from_os()
    workspace = Path(args.path).resolve() if getattr(args, "path", None) else Path.cwd()
    exported_clank_id = env.get("CLANKOPS_CLANK_ID")
    if not exported_clank_id and env.get("CLANKOPS_SESSION_ID"):
        try:
            exported_clank_id = store.resolve_session(env["CLANKOPS_SESSION_ID"])["clank_id"]
        except ClankOpsError:
            exported_clank_id = None
    try:
        expected = _resolve_expected_clank(
            store,
            explicit=getattr(args, "clank", None),
            workspace=workspace,
            exported_clank_id=exported_clank_id,
        )
        file_ctx = read_clank_context(expected["clank_id"])
        if env.get("CLANKOPS_SESSION_ID"):
            session_id = env.get("CLANKOPS_SESSION_ID")
            mission_id = env.get("CLANKOPS_MISSION_ID")
        elif file_ctx and file_ctx.get("active") and not file_ctx.get("session_ended"):
            session_id = file_ctx.get("session_id")
            mission_id = file_ctx.get("mission_id")
        else:
            session_id = None
            mission_id = None
        row = validate_session_env(
            store,
            session_id=session_id,
            mission_id=mission_id,
            clank_id=expected["clank_id"],
            actor=args.actor,
        )
    except ClankOpsError as exc:
        sys.stderr.write(f"CLANKOPS INTEGRATION FAILED\nerror: {exc}\n{RECOVERY_HINT}\n")
        store.conn.close()
        return 2
    file_ctx = read_clank_context(expected["clank_id"]) or {}
    payload = {
        "ok": True,
        "session_id": row["session_id"],
        "mission_id": row["mission_id"],
        "clank_id": row["clank_id"],
        "clank_slug": expected.get("slug"),
        "actor": row["actor"],
        "env": env,
        "context_file": str(file_ctx.get("context_file") or ""),
    }
    _print(
        "ClankOps context is valid\n" + format_powershell_env(file_ctx or {"env": env}),
        as_json=args.json,
        payload=payload,
    )
    store.conn.close()
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    store = _store(args)
    evidence = _git_evidence(store, args.mission)
    result = store.handoff_mission(
        args.mission,
        args.state,
        completed=args.completed,
        current_work=args.current,
        next_action=args.next,
        outstanding=_split_csv(args.outstanding),
        blockers=_split_csv(args.blockers),
        tests=args.tests,
        branch=args.branch,
        head=args.head,
        working_tree=args.working_tree,
        git_evidence=evidence,
        notes=args.notes,
        actor=args.actor,
        source=args.source,
    )
    session_row = store.resolve_session(
        result["checkpoint"]["session_id"]
    ) if result["checkpoint"].get("session_id") else None
    if session_row:
        _persist_work_context(store, args, result["mission"], session_row)
    _print(
        f"handoff {result['mission']['display_id']} -> {result['mission']['state']} "
        f"checkpoint={result['checkpoint']['checkpoint_id']}",
        as_json=args.json,
        payload=result,
    )
    store.conn.close()
    return 0


def cmd_ref_add(args: argparse.Namespace) -> int:
    store = _store(args)
    event = store.update_ref(
        args.clank,
        args.kind,
        args.value,
        canonical=args.canonical,
        actor=args.actor,
        source=args.source,
    )
    _print(
        f"ref {args.kind} {args.value}",
        as_json=args.json,
        payload={"event_id": event.event_id, "kind": args.kind, "value": args.value},
    )
    store.conn.close()
    return 0


def cmd_rel_add(args: argparse.Namespace) -> int:
    store = _store(args)
    row = store.add_relationship(
        args.from_clank,
        args.kind,
        args.to_clank,
        actor=args.actor,
        source=args.source,
    )
    _print(
        f"relationship {row['kind']} {row['from_clank_id']} -> {row['to_clank_id']}",
        as_json=args.json,
        payload=row,
    )
    store.conn.close()
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    store = _store(args)
    evidence = _git_evidence(store, args.mission) if args.capture_git else None
    row = store.record_checkpoint(
        args.mission,
        completed=args.completed,
        current_work=args.current,
        next_action=args.next,
        outstanding=_split_csv(args.outstanding),
        blockers=_split_csv(args.blockers),
        tests=args.tests,
        branch=args.branch,
        head=args.head,
        working_tree=args.working_tree,
        git_evidence=evidence,
        notes=args.notes,
        actor=args.actor,
        source=args.source,
    )
    if row.get("session_id"):
        _persist_work_context(
            store,
            args,
            store.resolve_mission(args.mission),
            store.resolve_session(row["session_id"]),
        )
    _print(
        f"checkpoint {row['checkpoint_id']} recorded for mission",
        as_json=args.json,
        payload=row,
    )
    store.conn.close()
    return 0


def cmd_task_add(args: argparse.Namespace) -> int:
    store = _store(args)
    row = store.add_task(args.mission, args.title, actor=args.actor)
    _print(f"task {row['task_id']} [{row['state']}] {row['title']}", as_json=args.json, payload=row)
    store.conn.close()
    return 0


def cmd_task_done(args: argparse.Namespace) -> int:
    store = _store(args)
    row = store.transition_task(args.task, "DONE", actor=args.actor)
    _print(f"task {row['task_id']} -> {row['state']}", as_json=args.json, payload=row)
    store.conn.close()
    return 0


def cmd_feature_add(args: argparse.Namespace) -> int:
    store = _store(args)
    row = store.add_feature(
        args.clank,
        args.name,
        state=args.state or FeatureState.PRESENT,
        actor=args.actor,
    )
    _print(
        f"feature {row['feature_id']} [{row['state']}] {row['name']}",
        as_json=args.json,
        payload=row,
    )
    store.conn.close()
    return 0


def cmd_decision_add(args: argparse.Namespace) -> int:
    store = _store(args)
    row = store.add_decision(
        args.mission,
        args.statement,
        why=args.why,
        alternatives=args.alternatives,
        actor=args.actor,
    )
    _print(f"decision {row['decision_id']} recorded", as_json=args.json, payload=row)
    store.conn.close()
    return 0


def cmd_blocker_add(args: argparse.Namespace) -> int:
    store = _store(args)
    row = store.add_blocker(args.mission, args.description, actor=args.actor)
    _print(f"blocker {row['blocker_id']} open: {row['description']}", as_json=args.json, payload=row)
    store.conn.close()
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    store = _store(args)
    brief = store.brief(args.clank)
    _print(format_brief(brief), as_json=args.json, payload=brief)
    store.conn.close()
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    store = _store(args)
    events = store.history(args.clank, limit=args.limit)
    payload = [
        {
            "event_id": e.event_id,
            "ts_utc": e.ts_utc,
            "event_type": e.event_type,
            "actor": e.actor,
            "source": e.source,
            "session_id": e.session_id,
            "ledger_seq": e.ledger_seq,
            "payload": e.payload,
            "provenance": e.provenance,
        }
        for e in events
    ]
    _print(format_history(events), as_json=args.json, payload=payload)
    store.conn.close()
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    store = _store(args)
    count = store.rebuild()
    _print(f"rebuilt projections from {count} events", as_json=args.json, payload={"events": count})
    store.conn.close()
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    store = _store(args)
    action = args.session_action
    if action == "start":
        row = store.start_session(args.mission, actor=args.actor, source=args.source)
        _print(
            f"session {row['session_id']} started actor={row['actor']}",
            as_json=args.json,
            payload=row,
        )
    elif action == "end":
        row = store.end_session(args.session_id, actor=args.actor, reason=args.reason)
        _print(
            f"session {row['session_id']} ended",
            as_json=args.json,
            payload=row,
        )
    elif action == "list":
        rows = store.list_sessions(args.mission)
        if args.json:
            _print("", as_json=True, payload=rows)
        else:
            if not rows:
                sys.stdout.write("No sessions.\n")
            for row in rows:
                end = row["ended_utc"] or "open"
                sys.stdout.write(
                    f"{row['session_id']} actor={row['actor']} {row['started_utc']} -> {end}\n"
                )
    else:
        raise ClankOpsError(f"unknown session action {action}")
    store.conn.close()
    return 0


def cmd_artifact_add(args: argparse.Namespace) -> int:
    store = _store(args)
    row = store.attach_artifact(
        mission=args.mission,
        kind=args.kind,
        ref=args.ref,
        title=args.title,
        actor=args.actor,
        source=args.source,
    )
    _print(
        f"artifact {row['artifact_id']} {row['kind']} {row['ref']}",
        as_json=args.json,
        payload=row,
    )
    store.conn.close()
    return 0


def cmd_census_import_file(args: argparse.Namespace) -> int:
    census = load_census(args.file)
    store = _store(args)
    stats = store.import_census(census, actor=args.actor, source=EventSource.RECONSTRUCTED)
    _print(f"imported {stats}", as_json=args.json, payload=stats)
    store.conn.close()
    return 0


def cmd_fleet_adopt_verified(args: argparse.Namespace) -> int:
    census = load_census(args.file)
    store = _store(args)
    stats = store.adopt_verified_census(census, actor=args.actor, source=EventSource.RECONSTRUCTED)
    _print(
        f"adopted verified fleet {stats}",
        as_json=args.json,
        payload=stats,
    )
    store.conn.close()
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    from clankops.readmodel import coverage_report

    census = load_census(args.file)
    store = open_readonly_store(args.db)
    try:
        report = coverage_report(store, census)
    finally:
        store.conn.close()
    if args.json:
        _print("", as_json=True, payload=report)
        return 0
    unresolved = report.get("verified_unresolved") or []
    unresolved_text = ", ".join(str(item.get("slug") or "?") for item in unresolved) or "none"
    lines = [
        f"census candidates: {report['census_candidates']}",
        f"registered Clanks: {report['registered_clanks']}",
        f"VERIFIED candidates: {report['verified_candidates']}",
        f"VERIFIED registered: {report['verified_registered']}",
        f"VERIFIED unresolved: {report['verified_unresolved_count']} ({unresolved_text})",
        f"PROBABLE: {report['probable']}",
        f"UNKNOWN: {report['unknown']}",
        f"SUPPORT_COMPONENT: {report['support_component']}",
        f"NOT_A_CLANK: {report['not_a_clank']}",
        f"NEEDS_RECONSTRUCTION: {report['needs_reconstruction']}",
        f"duplicate identity groups: {report['duplicate_identity_groups']}",
        report["verified_denominator_note"],
    ]
    for group in report.get("duplicates") or []:
        lines.append(
            f"  duplicate {group.get('identity')}: {group.get('path_count')} paths"
        )
    _print("\n".join(lines), as_json=False)
    return 0


def _format_session_row(row: dict[str, Any]) -> str:
    anomaly = f" ANOMALY={row['anomaly']}" if row.get("anomaly") else ""
    stale = " [stale]" if row.get("stale") else ""
    return (
        f"{row['session_id']} actor={row.get('actor') or 'unknown'} "
        f"clank={row.get('clank_slug') or 'unknown'} "
        f"mission={row.get('mission_display') or 'unknown'} "
        f"[{row.get('mission_state') or 'unknown'}] "
        f"started={row.get('started_utc') or 'unknown'} age={row.get('age') or 'unknown'} "
        f"checkpoint={row.get('latest_checkpoint_utc') or 'unknown'} "
        f"branch={row.get('branch') or 'unknown'} "
        f"HEAD={row.get('head_short') or row.get('head') or 'unknown'} "
        f"next={row.get('next_action') or 'unknown'}"
        f"{stale}{anomaly}"
    )


def cmd_sessions_open(args: argparse.Namespace) -> int:
    from clankops.readmodel import open_sessions

    store = open_readonly_store(args.db)
    try:
        rows = open_sessions(store)
    finally:
        store.conn.close()
    if args.json:
        _print("", as_json=True, payload=rows)
        return 0
    if not rows:
        _print("No open Sessions.", as_json=False)
        return 0
    _print("\n".join(_format_session_row(row) for row in rows), as_json=False)
    return 0


def cmd_sessions_stale(args: argparse.Namespace) -> int:
    from clankops.readmodel import stale_sessions
    from clankops.timefmt import parse_duration

    older = parse_duration(args.older_than)
    store = open_readonly_store(args.db)
    try:
        rows = stale_sessions(store, older_than=older)
    finally:
        store.conn.close()
    if args.json:
        _print("", as_json=True, payload=rows)
        return 0
    if not rows:
        _print(f"No stale Sessions older than {args.older_than}.", as_json=False)
        return 0
    _print("\n".join(_format_session_row(row) for row in rows), as_json=False)
    return 0


def cmd_terminal(args: argparse.Namespace) -> int:
    from clankops.terminal import DEFAULT_HOST, serve
    from clankops.timefmt import parse_duration

    host = args.host or DEFAULT_HOST
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValidationError("Terminal alpha binds localhost only")
    census_path = Path(args.census) if args.census else default_census_path()
    httpd = serve(
        db_path=args.db,
        host=host,
        port=args.port,
        census_path=census_path,
        stale_after=parse_duration(args.stale_after),
    )
    bound = httpd.server_address
    _print(
        f"ClankOps Terminal (read-only) http://{bound[0]}:{bound[1]}/",
        as_json=args.json,
        payload={
            "host": bound[0],
            "port": bound[1],
            "read_only": True,
            "connection": "per-request",
            "stale_after": args.stale_after,
            "census": str(census_path),
        },
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clankctl",
        description="ClankOps development ledger (Foundation 2)",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="SQLite database path (default: %%USERPROFILE%%/.clankops/clankops.db or $CLANKOPS_DB)",
    )
    parser.add_argument("--actor", default=os.environ.get("CLANKOPS_ACTOR") or "user")
    parser.add_argument(
        "--source",
        default=None,
        choices=[s.value for s in EventSource],
        help="Evidence provenance (default: USER for --actor user/operator, else AGENT_REPORT)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--session",
        default=None,
        help="Session UUID to attribute mutations (or $CLANKOPS_SESSION_ID)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create or migrate the database")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("census", help="scan local/GitHub candidates (read-only)")
    p.add_argument("--roots", nargs="*", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--import", dest="do_import", action="store_true")
    p.add_argument("--no-github", action="store_true")
    p.set_defaults(func=cmd_census)

    p = sub.add_parser("census-import", help="import an existing census JSON artefact")
    p.add_argument("file")
    p.set_defaults(func=cmd_census_import_file)

    p = sub.add_parser(
        "fleet-adopt-verified",
        help="register remaining VERIFIED census Clanks; extra checkouts become refs",
    )
    p.add_argument(
        "--file",
        default=str(Path("data/bootstrap/clank_census.json")),
        help="census JSON (default: data/bootstrap/clank_census.json)",
    )
    p.set_defaults(func=cmd_fleet_adopt_verified)

    p = sub.add_parser(
        "coverage",
        help="read-only VERIFIED census coverage (does not promote candidates)",
    )
    p.add_argument(
        "--file",
        default=str(Path("data/bootstrap/clank_census.json")),
        help="census JSON (default: data/bootstrap/clank_census.json)",
    )
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("terminal", help="read-only localhost Clank Terminal (alpha)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument(
        "--census",
        default=None,
        help="census JSON for VERIFIED coverage chips (default: data/bootstrap/clank_census.json)",
    )
    p.add_argument(
        "--stale-after",
        default="24h",
        help="open Session age treated as stale on the fleet home (default: 24h)",
    )
    p.set_defaults(func=cmd_terminal)

    p = sub.add_parser("list", help="list registered Clanks")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show a Clank")
    p.add_argument("clank")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("register", help="register a Clank identity")
    p.add_argument("clank")
    p.add_argument("--name")
    p.add_argument("--description")
    p.add_argument("--path")
    p.add_argument("--remote")
    p.add_argument("--alias", action="append", default=[])
    p.add_argument("--lifecycle", default="UNKNOWN")
    p.set_defaults(func=cmd_register)

    mission = sub.add_parser("mission", help="mission commands")
    msub = mission.add_subparsers(dest="mission_action", required=True)
    p = msub.add_parser("start")
    p.add_argument("clank")
    p.add_argument("objective")
    p.set_defaults(func=cmd_mission_start)
    p = msub.add_parser("list")
    p.add_argument("clank")
    p.set_defaults(func=cmd_mission_list)
    for action in ("pause", "resume", "complete", "abandon", "block"):
        p = msub.add_parser(action)
        p.add_argument("mission")
        p.set_defaults(func=cmd_mission_transition)

    work = sub.add_parser("work", help="start or resume a Cursor development Session")
    wsub = work.add_subparsers(dest="work_action", required=True)
    p = wsub.add_parser("start", help="explicitly create a new Mission and open a Session")
    p.add_argument("clank")
    p.add_argument("objective")
    p.add_argument(
        "--new-mission",
        action="store_true",
        help="create a new Mission even if an unfinished one exists",
    )
    p.set_defaults(func=cmd_work_start)
    p = wsub.add_parser("resume", help="resume the unfinished Mission (does not invent a new one)")
    p.add_argument("target", help="Clank slug or Mission id")
    p.set_defaults(func=cmd_work_resume)
    p = wsub.add_parser("env", help="validate Session context for this Clank/workspace")
    p.add_argument("clank", nargs="?", default=None, help="expected Clank slug or id")
    p.add_argument(
        "--path",
        default=None,
        help="workspace path to identify the Clank (default: current directory)",
    )
    p.set_defaults(func=cmd_work_env)

    p = sub.add_parser("handoff", help="checkpoint, capture git, and pause/block/complete/abandon")
    p.add_argument("mission")
    p.add_argument(
        "--state",
        default=MissionState.PAUSED,
        choices=[
            MissionState.PAUSED,
            MissionState.BLOCKED,
            MissionState.COMPLETED,
            MissionState.ABANDONED,
        ],
    )
    p.add_argument("--completed")
    p.add_argument("--current")
    p.add_argument("--next")
    p.add_argument("--outstanding", help="comma-separated outstanding work")
    p.add_argument("--blockers", help="comma-separated blocker notes")
    p.add_argument("--tests")
    p.add_argument("--branch")
    p.add_argument("--head")
    p.add_argument("--working-tree")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_handoff)

    p = sub.add_parser("ref", help="add a Clank ref (path, remote) without creating a new identity")
    rsub = p.add_subparsers(dest="ref_action", required=True)
    p = rsub.add_parser("add")
    p.add_argument("clank")
    p.add_argument("kind")
    p.add_argument("value")
    p.add_argument("--canonical", action="store_true")
    p.set_defaults(func=cmd_ref_add)

    p = sub.add_parser("rel", help="record a relationship between two Clanks")
    rsub = p.add_subparsers(dest="rel_action", required=True)
    p = rsub.add_parser("add")
    p.add_argument("from_clank")
    p.add_argument("kind")
    p.add_argument("to_clank")
    p.set_defaults(func=cmd_rel_add)

    p = sub.add_parser("checkpoint", help="record a development checkpoint")
    p.add_argument("mission")
    p.add_argument("--completed")
    p.add_argument("--current")
    p.add_argument("--next")
    p.add_argument("--outstanding", help="comma-separated outstanding work")
    p.add_argument("--blockers", help="comma-separated blocker notes")
    p.add_argument("--tests")
    p.add_argument("--branch")
    p.add_argument("--head")
    p.add_argument("--working-tree")
    p.add_argument("--notes")
    p.add_argument("--capture-git", action="store_true")
    p.set_defaults(func=cmd_checkpoint)

    task = sub.add_parser("task", help="task commands")
    tsub = task.add_subparsers(dest="task_action", required=True)
    p = tsub.add_parser("add")
    p.add_argument("mission")
    p.add_argument("title")
    p.set_defaults(func=cmd_task_add)
    p = tsub.add_parser("done")
    p.add_argument("task")
    p.set_defaults(func=cmd_task_done)

    feat = sub.add_parser("feature", help="feature commands")
    fsub = feat.add_subparsers(dest="feature_action", required=True)
    p = fsub.add_parser("add")
    p.add_argument("clank")
    p.add_argument("name")
    p.add_argument("--state", default="PRESENT")
    p.set_defaults(func=cmd_feature_add)

    dec = sub.add_parser("decision", help="decision commands")
    dsub = dec.add_subparsers(dest="decision_action", required=True)
    p = dsub.add_parser("add")
    p.add_argument("mission")
    p.add_argument("statement")
    p.add_argument("--why")
    p.add_argument("--alternatives")
    p.set_defaults(func=cmd_decision_add)

    blk = sub.add_parser("blocker", help="blocker commands")
    bsub = blk.add_subparsers(dest="blocker_action", required=True)
    p = bsub.add_parser("add")
    p.add_argument("mission")
    p.add_argument("description")
    p.set_defaults(func=cmd_blocker_add)

    sess = sub.add_parser("session", help="session commands")
    ssub = sess.add_subparsers(dest="session_action", required=True)
    p = ssub.add_parser("start")
    p.add_argument("mission")
    p.set_defaults(func=cmd_session)
    p = ssub.add_parser("end")
    p.add_argument("session_id")
    p.add_argument("--reason")
    p.set_defaults(func=cmd_session)
    p = ssub.add_parser("list")
    p.add_argument("mission")
    p.set_defaults(func=cmd_session)

    sessions = sub.add_parser("sessions", help="read-only Session observability")
    sess_sub = sessions.add_subparsers(dest="sessions_command", required=True)
    p = sess_sub.add_parser("open", help="Sessions with ended_utc IS NULL")
    p.set_defaults(func=cmd_sessions_open)
    p = sess_sub.add_parser("stale", help="open Sessions older than a threshold (does not close them)")
    p.add_argument("--older-than", default="24h", help="age threshold (default: 24h)")
    p.set_defaults(func=cmd_sessions_stale)

    p = sub.add_parser("artifact", help="attach an external artefact")
    asub = p.add_subparsers(dest="artifact_action", required=True)
    p = asub.add_parser("add")
    p.add_argument("mission")
    p.add_argument("kind")
    p.add_argument("ref")
    p.add_argument("--title")
    p.set_defaults(func=cmd_artifact_add)

    p = sub.add_parser("brief", help="resume brief for a Clank")
    p.add_argument("clank")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("history", help="event history for a Clank")
    p.add_argument("clank")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("rebuild", help="rebuild projections from the event log")
    p.set_defaults(func=cmd_rebuild)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        args.source = resolve_invocation_source(args.actor, args.source)
        return int(args.func(args))
    except ClankOpsError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
