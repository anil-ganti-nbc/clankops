"""Foundation 6: durable deployment/runtime observations.

Captured evidence only. No live SSH to Hetzner or NAS.
"""

from __future__ import annotations

import json
from pathlib import Path

from clankops.ci import capture_ci
from clankops.cli import main
from clankops.deployment import (
    ENVIRONMENTS,
    capture_deployment,
    current_deployments,
    list_deployments,
)
from clankops.enums import EventSource, EventType
from clankops.errors import ValidationError
from clankops.projections import dump_projection_state, rebuild_projections
from clankops.readmodel import dossier, ledger_fingerprint
from clankops.redact import sanitize_captured
from clankops.reconcile import reconcile_clank
from clankops.store import open_store
from clankops.terminal import _dossier_html

from test_foundation3 import CANON, HEAD, _github, _seed
from test_foundation5 import _remote_with_checks

import pytest

HETZNER_SHA = "24d61dd4238ad03c073ae592c57e2ffa6fbdb488"
NAS_SHA = "d720e0635894ddcc9a39f116e2aa4a1768090042"
HETZNER_HOST = "ubuntu-4gb-hel1-1"
NAS_HOST = "Anil_NAS"
HETZNER_SURFACE = "hetzner-prod"
NAS_SURFACE = "nas-canary"
WEBHOOK_URL = "https://discord.com/api/webhooks/000000000000000000/do-not-store-this"
OBSERVED_HOW = "operator report from COPS-000012; no live SSH in this capture"
GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz012345"
BEARER_SECRET = "Bearer super-bearer-secret-value"
QUERY_SECRET = "https://example.test/hook?token=query-token-secret&api_key=query-api-key-secret"


def _hetzner(**overrides):
    payload = {
        "surface": HETZNER_SURFACE,
        "environment": "prod",
        "host": HETZNER_HOST,
        "runtime_path": "/home/deploy/staging/oem-radar",
        "deployed_sha": HETZNER_SHA,
        "image": f"oem-radar:{HETZNER_SHA}",
        "runtime_identity": "oem-radar",
        "deployed": "yes",
        "running": "scheduled_oneshot",
        "scheduler": "cron",
        "cadence": "20 * * * *",
        "state_store": "oem_radar_portability_data",
        "collection_authority": "yes",
        "notification_authority": "yes",
        "webhook_configured": "yes",
        "observed_how": OBSERVED_HOW,
        "notes": "production Discord sender; compose release_channel remains experimental",
    }
    payload.update(overrides)
    return payload


def _nas(**overrides):
    payload = {
        "surface": NAS_SURFACE,
        "environment": "canary",
        "host": NAS_HOST,
        "runtime_path": "/volume2/clank/oem-radar",
        "deployed_sha": NAS_SHA,
        "image": "oem-radar:d720e06",
        "runtime_identity": "oem-radar-canary",
        "deployed": "yes",
        "running": "scheduled",
        "scheduler": "dsm_task",
        "cadence": "hourly :15",
        "state_store": "/volume2/clank/oem-radar/state/radar.db",
        "collection_authority": "yes",
        "notification_authority": "no",
        "webhook_configured": "no",
        "sent_count": 0,
        "observed_how": OBSERVED_HOW,
        "notes": "canary collector; webhook absent; SENT 0",
    }
    payload.update(overrides)
    return payload


def _capture_topology(store):
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    hetzner = capture_deployment(store, "oem-radar", **_hetzner())
    nas = capture_deployment(store, "oem-radar", **_nas())
    return hetzner, nas


def _by_host(rows: list[dict]) -> dict[str, dict]:
    return {row["host_identity"]: row for row in rows}


def _by_surface(rows: list[dict]) -> dict[str, dict]:
    return {row["surface_id"]: row for row in rows}


def _ledger_blob(store) -> str:
    rows = store.conn.execute(
        "SELECT payload_json, provenance_json FROM events WHERE event_type = ?",
        (EventType.DEPLOYMENT_OBSERVED,),
    ).fetchall()
    parts = [row["payload_json"] + row["provenance_json"] for row in rows]
    proj = store.conn.execute("SELECT * FROM deployment_observations").fetchall()
    for row in proj:
        parts.append(json.dumps(dict(row), default=str))
    return "\n".join(parts)


def test_environments_are_the_closed_surface_set() -> None:
    assert ENVIRONMENTS == frozenset({"prod", "staging", "canary", "dev", "experimental"})


def test_oem_radar_topology_answers_the_acceptance_questions(tmp_path: Path) -> None:
    store = open_store(tmp_path / "oem.db", actor="cursor")
    _capture_topology(store)
    current = current_deployments(store, "oem-radar")
    hosts = _by_host(current)
    assert set(hosts) == {HETZNER_HOST, NAS_HOST}

    discord = [row for row in current if row["notification_authority"] == "yes"]
    assert [row["host_identity"] for row in discord] == [HETZNER_HOST]
    assert discord[0]["environment"] == "prod"

    prod = [row for row in current if row["environment"] == "prod"]
    assert len(prod) == 1
    assert prod[0]["deployed_sha"] == HETZNER_SHA
    assert prod[0]["deployed_sha"].startswith("24d61dd")

    assert hosts[NAS_HOST]["notification_authority"] == "no"
    assert hosts[NAS_HOST]["webhook_configured"] == "no"
    assert hosts[NAS_HOST]["sent_count"] == 0

    scheduled = [
        row["host_identity"]
        for row in current
        if row["running"] in {"scheduled", "scheduled_oneshot"}
    ]
    assert set(scheduled) == {HETZNER_HOST, NAS_HOST}

    shas = {row["deployed_sha"] for row in current}
    assert shas == {HETZNER_SHA, NAS_SHA}
    assert {row["surface_id"] for row in current} == {HETZNER_SURFACE, NAS_SURFACE}
    store.conn.close()


def test_source_head_is_not_deployed_head(tmp_path: Path) -> None:
    store = open_store(tmp_path / "head.db", actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    capture_deployment(store, "oem-radar", **_hetzner())
    current = current_deployments(store, "oem-radar")
    assert current[0]["deployed_sha"] == HETZNER_SHA
    assert current[0]["deployed_sha"] != HEAD
    store.conn.close()


def test_ci_success_is_not_deployment_success(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ci.db", actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    capture_ci(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert current_deployments(store, "oem-radar") == []
    capture_deployment(store, "oem-radar", **_hetzner())
    current = current_deployments(store, "oem-radar")
    assert len(current) == 1
    assert current[0]["deployed_sha"] != HEAD or current[0]["deployed_sha"] == HETZNER_SHA
    store.conn.close()


def test_running_is_not_authoritative(tmp_path: Path) -> None:
    store = open_store(tmp_path / "auth.db", actor="cursor")
    _capture_topology(store)
    nas = _by_host(current_deployments(store, "oem-radar"))[NAS_HOST]
    assert nas["deployed"] == "yes"
    assert nas["running"] == "scheduled"
    assert nas["collection_authority"] == "yes"
    assert nas["notification_authority"] == "no"
    store.conn.close()


def test_collection_and_notification_authority_are_independent(tmp_path: Path) -> None:
    store = open_store(tmp_path / "indep.db", actor="cursor")
    _capture_topology(store)
    hosts = _by_host(current_deployments(store, "oem-radar"))
    assert hosts[HETZNER_HOST]["collection_authority"] == "yes"
    assert hosts[HETZNER_HOST]["notification_authority"] == "yes"
    assert hosts[NAS_HOST]["collection_authority"] == "yes"
    assert hosts[NAS_HOST]["notification_authority"] == "no"
    store.conn.close()


def test_multiple_simultaneous_deployments_are_current(tmp_path: Path) -> None:
    store = open_store(tmp_path / "multi.db", actor="cursor")
    _capture_topology(store)
    current = current_deployments(store, "oem-radar")
    listed = list_deployments(store, "oem-radar")
    assert len(current) == 2
    assert [row["observation_id"] for row in listed] == [row["observation_id"] for row in current]
    store.conn.close()


def test_later_capture_supersedes_read_model_without_rewriting_history(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path / "immut.db", actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    first = capture_deployment(store, "oem-radar", **_hetzner())
    second = capture_deployment(
        store,
        "oem-radar",
        **_hetzner(deployed_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", notes="later soak"),
    )
    assert first["rewrote_history"] is False
    assert second["rewrote_history"] is False
    assert first["observation"]["observation_id"] != second["observation"]["observation_id"]

    events = store.conn.execute(
        "SELECT payload_json FROM events WHERE event_type = ? ORDER BY ledger_seq",
        (EventType.DEPLOYMENT_OBSERVED,),
    ).fetchall()
    assert len(events) == 2
    assert json.loads(events[0]["payload_json"])["deployed_sha"] == HETZNER_SHA
    assert json.loads(events[1]["payload_json"])["deployed_sha"].startswith("aaaa")

    current = current_deployments(store, "oem-radar")
    assert len(current) == 1
    assert current[0]["deployed_sha"].startswith("aaaa")
    assert current[0]["observation_id"] == second["observation"]["observation_id"]

    history = list_deployments(store, "oem-radar", history=True)
    assert [row["deployed_sha"] for row in history] == [
        HETZNER_SHA,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ]
    assert history[0]["is_current"] is False
    assert history[1]["is_current"] is True
    store.conn.close()


def test_unknown_stays_unknown_when_fields_omitted(tmp_path: Path) -> None:
    store = open_store(tmp_path / "unk.db", actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    capture_deployment(
        store,
        "oem-radar",
        surface="laptop-dev",
        environment="dev",
        host="laptop",
        observed_how="agent report of a local tree; runtime not inspected",
    )
    row = current_deployments(store, "oem-radar")[0]
    assert row["deployed"] == "unknown"
    assert row["running"] == "unknown"
    assert row["deployed_sha"] is None
    assert row["collection_authority"] == "unknown"
    assert row["notification_authority"] == "unknown"
    assert row["webhook_configured"] == "unknown"
    assert row["scheduler"] == "unknown"
    assert row["sent_count"] is None
    store.conn.close()


def test_secrets_are_never_stored(tmp_path: Path) -> None:
    store = open_store(tmp_path / "sec.db", actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    before = ledger_fingerprint(store)
    capture_deployment(
        store,
        "oem-radar",
        **_hetzner(
            host=WEBHOOK_URL,
            runtime_path=QUERY_SECRET,
            image=BEARER_SECRET,
            runtime_identity=GITHUB_TOKEN,
            cadence=f"hourly {QUERY_SECRET}",
            state_store="https://user:store-password-secret@nas.example.test/data",
            observed_how=f"{OBSERVED_HOW} {BEARER_SECRET}",
            observer=GITHUB_TOKEN,
            notes=f"webhook {WEBHOOK_URL} {BEARER_SECRET}",
            metadata={
                "OEM_RADAR_DISCORD_WEBHOOK": WEBHOOK_URL,
                "webhook_url": WEBHOOK_URL,
                "token": GITHUB_TOKEN,
                "url": WEBHOOK_URL,
                "value": GITHUB_TOKEN,
                "nested": {"url": WEBHOOK_URL, "value": "ghp_nestedsecretvaluexx"},
                "homepage": "https://github.com/anil-ganti-nbc/oem-radar",
                "webhook_configured": True,
            },
        ),
    )
    blob = _ledger_blob(store) + json.dumps(current_deployments(store, "oem-radar"), default=str)
    for secret in (
        "do-not-store-this",
        "discord.com/api/webhooks",
        "query-token-secret",
        "query-api-key-secret",
        "super-bearer-secret-value",
        GITHUB_TOKEN,
        "store-password-secret",
        "ghp_nestedsecretvaluexx",
    ):
        assert secret not in blob
    meta = current_deployments(store, "oem-radar")[0]["metadata"]
    assert meta["webhook_configured"] is True
    assert meta["homepage"] == "https://github.com/anil-ganti-nbc/oem-radar"
    assert meta["url"] == "[redacted]"
    assert meta["value"] == "[redacted]"
    assert meta["nested"]["url"] == "[redacted]"
    assert meta["nested"]["value"] == "[redacted]"
    assert ledger_fingerprint(store)["event_count"] == before["event_count"] + 1
    store.conn.close()


def test_sanitize_captured_keeps_boolean_webhook_flags() -> None:
    cleaned = sanitize_captured(
        {"webhook_configured": "yes", "webhook_url": WEBHOOK_URL, "ok": True}
    )
    assert cleaned["webhook_configured"] == "yes"
    assert cleaned["webhook_url"] == "[redacted]"
    assert cleaned["ok"] is True


def test_runtime_evidence_states_how_it_was_observed(tmp_path: Path) -> None:
    store = open_store(tmp_path / "how.db", actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    with pytest.raises(ValidationError, match="observed-how"):
        capture_deployment(
            store,
            "oem-radar",
            surface=HETZNER_SURFACE,
            environment="prod",
            host=HETZNER_HOST,
        )
    result = capture_deployment(store, "oem-radar", **_hetzner())
    row = result["observation"]
    assert row["observed_how"] == OBSERVED_HOW
    assert row["source"] == EventSource.DEPLOYMENT
    event = store.conn.execute(
        "SELECT source, provenance_json FROM events WHERE event_type = ?",
        (EventType.DEPLOYMENT_OBSERVED,),
    ).fetchone()
    assert event["source"] == EventSource.DEPLOYMENT
    assert OBSERVED_HOW in event["provenance_json"]
    store.conn.close()


def test_reconcile_does_not_write_deployments(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ro.db", actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    before = ledger_fingerprint(store)
    reconcile_clank(store, "oem-radar", inspect_remote=_github())
    assert ledger_fingerprint(store) == before
    assert current_deployments(store, "oem-radar") == []
    store.conn.close()


def test_capture_requires_unfinished_mission(tmp_path: Path) -> None:
    store = open_store(tmp_path / "none.db", actor="cursor")
    store.register_clank("oem-radar")
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="no unfinished Mission"):
        capture_deployment(store, "oem-radar", **_hetzner())
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_capture_refuses_completed_and_abandoned_without_explicit(tmp_path: Path) -> None:
    store = open_store(tmp_path / "done.db", actor="cursor")
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.complete_mission(mission["display_id"])
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="no unfinished Mission"):
        capture_deployment(store, "oem-radar", **_hetzner())
    assert ledger_fingerprint(store) == before
    store.conn.close()

    store = open_store(tmp_path / "abandoned.db", actor="cursor")
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.abandon_mission(mission["display_id"])
    with pytest.raises(ValidationError, match="no unfinished Mission"):
        capture_deployment(store, "oem-radar", **_hetzner())
    store.conn.close()


def test_capture_attaches_to_single_paused_mission(tmp_path: Path) -> None:
    store = open_store(tmp_path / "paused.db", actor="cursor")
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.pause_mission(mission["display_id"])
    result = capture_deployment(store, "oem-radar", **_hetzner())
    assert result["mission_display"] == mission["display_id"]
    store.conn.close()


def test_capture_two_unfinished_missions_without_explicit_is_ambiguous(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path / "ambig.db", actor="cursor")
    first = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    second = store.start_mission("oem-radar", "second objective")
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="multiple unfinished Missions"):
        capture_deployment(store, "oem-radar", **_hetzner())
    assert ledger_fingerprint(store) == before
    assert first["display_id"] != second["display_id"]
    store.conn.close()


def test_capture_explicit_unfinished_mission_succeeds(tmp_path: Path) -> None:
    store = open_store(tmp_path / "explicit.db", actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    second = store.start_mission("oem-radar", "model COPS-000012 topology")
    result = capture_deployment(
        store,
        "oem-radar",
        mission=second["display_id"],
        **_hetzner(),
    )
    assert result["mission_display"] == second["display_id"]
    store.conn.close()


def test_capture_explicit_mission_for_other_clank_fails(tmp_path: Path) -> None:
    store = open_store(tmp_path / "other.db", actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.register_clank("watch-clank")
    other = store.start_mission("watch-clank", "health")
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="belongs to watch-clank"):
        capture_deployment(
            store,
            "oem-radar",
            mission=other["display_id"],
            **_hetzner(),
        )
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_capture_explicit_completed_mission_fails(tmp_path: Path) -> None:
    store = open_store(tmp_path / "explicit-done.db", actor="cursor")
    done = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.complete_mission(done["display_id"])
    store.start_mission("oem-radar", "still open")
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="COMPLETED"):
        capture_deployment(
            store,
            "oem-radar",
            mission=done["display_id"],
            **_hetzner(),
        )
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_rebuild_preserves_deployment_observations(tmp_path: Path) -> None:
    store = open_store(tmp_path / "rebuild.db", actor="cursor")
    _capture_topology(store)
    before = dump_projection_state(store.conn)
    rebuild_projections(store.conn)
    after = dump_projection_state(store.conn)
    assert before["deployment_observations"] == after["deployment_observations"]
    current = current_deployments(store, "oem-radar")
    assert {row["host_identity"] for row in current} == {HETZNER_HOST, NAS_HOST}
    store.conn.close()


def test_surface_environment_and_host_are_required(tmp_path: Path) -> None:
    store = open_store(tmp_path / "req.db", actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    with pytest.raises(ValidationError, match="surface"):
        capture_deployment(
            store,
            "oem-radar",
            environment="prod",
            host=HETZNER_HOST,
            observed_how=OBSERVED_HOW,
        )
    with pytest.raises(ValidationError, match="environment"):
        capture_deployment(
            store,
            "oem-radar",
            surface=HETZNER_SURFACE,
            host=HETZNER_HOST,
            observed_how=OBSERVED_HOW,
        )
    with pytest.raises(ValidationError, match="host"):
        capture_deployment(
            store,
            "oem-radar",
            surface=HETZNER_SURFACE,
            environment="prod",
            observed_how=OBSERVED_HOW,
        )
    with pytest.raises(ValidationError, match="environment"):
        capture_deployment(
            store,
            "oem-radar",
            surface=HETZNER_SURFACE,
            environment="production",
            host=HETZNER_HOST,
            observed_how=OBSERVED_HOW,
        )
    with pytest.raises(ValidationError, match="surface"):
        capture_deployment(
            store,
            "oem-radar",
            surface=WEBHOOK_URL,
            environment="prod",
            host=HETZNER_HOST,
            observed_how=OBSERVED_HOW,
        )
    store.conn.close()


def test_dossier_and_terminal_show_deployments_without_colour_alone(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path / "term.db", actor="cursor")
    _capture_topology(store)
    payload = dossier(store, "oem-radar", inspect_remote=_github())
    rows = payload["deployments"]
    assert {row["host_identity"] for row in rows} == {HETZNER_HOST, NAS_HOST}
    html = _dossier_html(payload)
    assert "DEPLOYMENTS" in html
    assert HETZNER_SURFACE in html
    assert NAS_SURFACE in html
    assert HETZNER_HOST in html
    assert NAS_HOST in html
    assert "24d61dd" in html
    assert "d720e06" in html
    assert "notification: yes" in html
    assert "notification: no" in html
    assert "collection: yes" in html
    assert "scheduled_oneshot" in html
    assert "cron" in html
    assert "dsm_task" in html
    assert "DEPLOYMENT" in html
    assert OBSERVED_HOW in html
    store.conn.close()


def test_cli_capture_list_current(tmp_path: Path, capsys) -> None:
    db = str(tmp_path / "cli.db")
    assert main(["--db", db, "register", "oem-radar"]) == 0
    assert main(["--db", db, "mission", "start", "oem-radar", "model COPS-000012"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "cursor",
                "--json",
                "deployment",
                "capture",
                "oem-radar",
                "--surface",
                HETZNER_SURFACE,
                "--environment",
                "prod",
                "--host",
                HETZNER_HOST,
                "--runtime-path",
                "/home/deploy/staging/oem-radar",
                "--deployed-sha",
                HETZNER_SHA,
                "--image",
                f"oem-radar:{HETZNER_SHA}",
                "--runtime-identity",
                "oem-radar",
                "--deployed",
                "yes",
                "--running",
                "scheduled_oneshot",
                "--scheduler",
                "cron",
                "--cadence",
                "20 * * * *",
                "--state-store",
                "oem_radar_portability_data",
                "--collection-authority",
                "yes",
                "--notification-authority",
                "yes",
                "--webhook-configured",
                "yes",
                "--observed-how",
                OBSERVED_HOW,
            ]
        )
        == 0
    )
    hetzner = json.loads(capsys.readouterr().out)
    assert hetzner["observation"]["source"] == EventSource.DEPLOYMENT
    assert hetzner["observation"]["surface_id"] == HETZNER_SURFACE
    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "cursor",
                "deployment",
                "capture",
                "oem-radar",
                "--surface",
                NAS_SURFACE,
                "--environment",
                "canary",
                "--host",
                NAS_HOST,
                "--deployed-sha",
                NAS_SHA,
                "--deployed",
                "yes",
                "--running",
                "scheduled",
                "--scheduler",
                "dsm_task",
                "--collection-authority",
                "yes",
                "--notification-authority",
                "no",
                "--webhook-configured",
                "no",
                "--sent-count",
                "0",
                "--observed-how",
                OBSERVED_HOW,
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["--db", db, "--json", "deployment", "current", "oem-radar"]) == 0
    current = json.loads(capsys.readouterr().out)
    assert {row["host_identity"] for row in current} == {HETZNER_HOST, NAS_HOST}
    assert main(["--db", db, "deployment", "list", "oem-radar"]) == 0
    listed = capsys.readouterr().out
    assert HETZNER_SURFACE in listed
    assert NAS_SURFACE in listed
    assert HETZNER_HOST in listed
    assert NAS_HOST in listed
    assert "24d61dd" in listed
    assert "notification=yes" in listed
    assert "notification=no" in listed
    assert listed.count("discord.com/api/webhooks") == 0


def test_surface_id_is_identity_not_host_or_environment(tmp_path: Path) -> None:
    store = open_store(tmp_path / "surf.db", actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    host = "japan-mini-pc"
    sitemap = capture_deployment(
        store,
        "oem-radar",
        surface="experimental-sitemap-soak",
        environment="experimental",
        host=host,
        deployed_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        runtime_path="/opt/sitemap",
        runtime_identity="sitemap-soak",
        observed_how=OBSERVED_HOW,
    )
    japan = capture_deployment(
        store,
        "oem-radar",
        surface="japan-mini-pc-soak",
        environment="experimental",
        host=host,
        deployed_sha="cccccccccccccccccccccccccccccccccccccccc",
        runtime_path="/opt/japan",
        runtime_identity="japan-soak",
        observed_how=OBSERVED_HOW,
    )
    current = current_deployments(store, "oem-radar")
    surfaces = _by_surface(current)
    assert set(surfaces) == {"experimental-sitemap-soak", "japan-mini-pc-soak"}
    assert surfaces["experimental-sitemap-soak"]["host_identity"] == host
    assert surfaces["japan-mini-pc-soak"]["host_identity"] == host
    assert surfaces["experimental-sitemap-soak"]["environment"] == "experimental"
    assert surfaces["japan-mini-pc-soak"]["environment"] == "experimental"

    capture_deployment(
        store,
        "oem-radar",
        surface="experimental-sitemap-soak",
        environment="experimental",
        host=host,
        deployed_sha="dddddddddddddddddddddddddddddddddddddddd",
        observed_how=OBSERVED_HOW,
    )
    current = current_deployments(store, "oem-radar")
    surfaces = _by_surface(current)
    assert len(current) == 2
    assert surfaces["experimental-sitemap-soak"]["deployed_sha"].startswith("dddd")
    assert surfaces["japan-mini-pc-soak"]["deployed_sha"].startswith("cccc")
    assert (
        surfaces["japan-mini-pc-soak"]["observation_id"]
        == japan["observation"]["observation_id"]
    )
    history = list_deployments(store, "oem-radar", history=True)
    assert len(history) == 3
    assert sitemap["observation"]["observation_id"] in {
        row["observation_id"] for row in history
    }
    store.conn.close()

