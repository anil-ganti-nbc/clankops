import pytest

from clankops.enums import MissionState
from clankops.errors import InvalidTransitionError, ValidationError


def test_mission_create_and_list(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "Add Samsung Japan collector")
    assert m["display_id"] == "COPS-000001"
    assert m["state"] == MissionState.ACTIVE
    listed = store.list_missions("oem-radar")
    assert len(listed) == 1


def test_mission_state_transitions(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "Implement QC bar")
    paused = store.pause_mission(m["display_id"])
    assert paused["state"] == MissionState.PAUSED
    resumed = store.resume_mission(m["display_id"])
    assert resumed["state"] == MissionState.ACTIVE
    done = store.complete_mission(m["display_id"])
    assert done["state"] == MissionState.COMPLETED


def test_invalid_complete_from_paused(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "x")
    store.pause_mission(m["display_id"])
    with pytest.raises(InvalidTransitionError):
        store.complete_mission(m["display_id"])


def test_cannot_resume_completed(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "x")
    store.complete_mission(m["display_id"])
    with pytest.raises(InvalidTransitionError):
        store.resume_mission(m["display_id"])


def test_abandon(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "old idea")
    abandoned = store.abandon_mission(m["display_id"])
    assert abandoned["state"] == MissionState.ABANDONED


def test_empty_objective_rejected(store) -> None:
    store.register_clank("oem-radar")
    with pytest.raises(ValidationError):
        store.start_mission("oem-radar", "   ")
