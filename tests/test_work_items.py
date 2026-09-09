from clankops.enums import BlockerState, FeatureState, TaskState
from clankops.errors import InvalidTransitionError
import pytest


def test_checkpoint_recording(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "handheld expansion")
    cp = store.record_checkpoint(
        m["display_id"],
        completed="added Sixunited sources",
        current_work="verify collection",
        next_action="run soak against live sources",
        outstanding=["docs"],
        tests="pytest passed",
        branch="expansion-handheld-sixunited-m1",
        head="adb04cc0",
        working_tree="dirty (1 paths)",
        notes="do not invent extra scope",
    )
    assert cp["next_action"] == "run soak against live sources"
    brief = store.brief("oem-radar")
    assert brief["next_action"] == "run soak against live sources"
    assert brief["branch"] == "expansion-handheld-sixunited-m1"
    assert brief["head"] == "adb04cc0"


def test_task_lifecycle(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "work")
    task = store.add_task(m["display_id"], "write tests")
    assert task["state"] == TaskState.TODO
    started = store.transition_task(task["task_id"], TaskState.IN_PROGRESS)
    assert started["state"] == TaskState.IN_PROGRESS
    done = store.transition_task(task["task_id"], TaskState.DONE)
    assert done["state"] == TaskState.DONE
    with pytest.raises(InvalidTransitionError):
        store.transition_task(task["task_id"], TaskState.TODO)


def test_feature_lifecycle(store) -> None:
    store.register_clank("oem-radar")
    feat = store.add_feature("oem-radar", "Chinese handheld OEM coverage", state=FeatureState.IN_PROGRESS)
    present = store.transition_feature(feat["feature_id"], FeatureState.PRESENT)
    assert present["state"] == FeatureState.PRESENT
    deprecated = store.transition_feature(feat["feature_id"], FeatureState.DEPRECATED)
    assert deprecated["state"] == FeatureState.DEPRECATED


def test_decision_and_blocker(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "architecture")
    d = store.add_decision(
        m["display_id"],
        "Keep ClankOps independent of Motherclank",
        why="separate development memory from fleet observation",
        alternatives="fold into Motherclank",
    )
    assert "independent" in d["statement"]
    b = store.add_blocker(m["display_id"], "waiting on operator ratification")
    assert b["state"] == BlockerState.OPEN
    resolved = store.resolve_open_blocker(b["blocker_id"], resolution="ratified")
    assert resolved["state"] == BlockerState.RESOLVED
