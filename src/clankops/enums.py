"""Closed enumerations for Foundation 0.

Relationship kinds are deliberately *not* a closed enum: new kinds must
be addable without schema surgery. A recommended vocabulary is listed
in RECOMMENDED_RELATIONSHIP_KINDS.
"""

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    CLANK_REGISTERED = "CLANK_REGISTERED"
    CLANK_ALIAS_ADDED = "CLANK_ALIAS_ADDED"
    CLANK_REF_UPDATED = "CLANK_REF_UPDATED"
    CLANK_LIFECYCLE_CHANGED = "CLANK_LIFECYCLE_CHANGED"
    MISSION_CREATED = "MISSION_CREATED"
    MISSION_STATE_CHANGED = "MISSION_STATE_CHANGED"
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_ENDED = "SESSION_ENDED"
    CHECKPOINT_RECORDED = "CHECKPOINT_RECORDED"
    TASK_CREATED = "TASK_CREATED"
    TASK_STATE_CHANGED = "TASK_STATE_CHANGED"
    FEATURE_ADDED = "FEATURE_ADDED"
    FEATURE_STATE_CHANGED = "FEATURE_STATE_CHANGED"
    DECISION_RECORDED = "DECISION_RECORDED"
    DECISION_SUPERSEDED = "DECISION_SUPERSEDED"
    BLOCKER_ADDED = "BLOCKER_ADDED"
    BLOCKER_RESOLVED = "BLOCKER_RESOLVED"
    ARTIFACT_ATTACHED = "ARTIFACT_ATTACHED"
    RELATIONSHIP_RECORDED = "RELATIONSHIP_RECORDED"
    CENSUS_CANDIDATE_RECORDED = "CENSUS_CANDIDATE_RECORDED"


class EventSource(StrEnum):
    USER = "USER"
    AGENT_REPORT = "AGENT_REPORT"
    LOCAL_GIT = "LOCAL_GIT"
    GITHUB = "GITHUB"
    CI = "CI"
    DEPLOYMENT = "DEPLOYMENT"
    SYSTEM = "SYSTEM"
    RECONSTRUCTED = "RECONSTRUCTED"


class ClankLifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    EXPERIMENTAL = "EXPERIMENTAL"
    PROTOTYPE = "PROTOTYPE"
    PERSONAL = "PERSONAL"
    SUPPORT = "SUPPORT"
    ABANDONED = "ABANDONED"
    SUPERSEDED = "SUPERSEDED"
    UNKNOWN = "UNKNOWN"


class MissionState(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
    SUPERSEDED = "SUPERSEDED"


class FeatureState(StrEnum):
    PROPOSED = "PROPOSED"
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    PRESENT = "PRESENT"
    DEPRECATED = "DEPRECATED"
    REMOVED = "REMOVED"
    REJECTED = "REJECTED"


class TaskState(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class BlockerState(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class CensusClassification(StrEnum):
    VERIFIED = "VERIFIED"
    PROBABLE = "PROBABLE"
    UNKNOWN = "UNKNOWN"
    SUPPORT_COMPONENT = "SUPPORT_COMPONENT"
    NOT_A_CLANK = "NOT_A_CLANK"
    NEEDS_RECONSTRUCTION = "NEEDS_RECONSTRUCTION"


class ArtifactKind(StrEnum):
    COMMIT = "COMMIT"
    BRANCH = "BRANCH"
    PULL_REQUEST = "PULL_REQUEST"
    ISSUE = "ISSUE"
    TEST_RUN = "TEST_RUN"
    FILE = "FILE"
    DOCUMENT = "DOCUMENT"
    DEPLOYMENT = "DEPLOYMENT"
    EXTERNAL = "EXTERNAL"
    WORKING_TREE = "WORKING_TREE"


RECOMMENDED_RELATIONSHIP_KINDS = (
    "DEPENDS_ON",
    "PROVIDES_TO",
    "SUPERSEDES",
    "REPLACED_BY",
    "SHARES_RUNTIME_WITH",
    "GOVERNED_BY",
    "DEPLOYED_WITH",
    "DUPLICATE_CHECKOUT_OF",
    "LOCAL_COPY_OF",
)

# Mission transitions. Terminal states still allow SUPERSEDED so history
# can record replacement without rewriting the completed/abandoned row.
MISSION_TRANSITIONS: dict[MissionState, frozenset[MissionState]] = {
    MissionState.PLANNED: frozenset(
        {
            MissionState.ACTIVE,
            MissionState.PAUSED,
            MissionState.BLOCKED,
            MissionState.ABANDONED,
            MissionState.SUPERSEDED,
        }
    ),
    MissionState.ACTIVE: frozenset(
        {
            MissionState.PAUSED,
            MissionState.BLOCKED,
            MissionState.COMPLETED,
            MissionState.ABANDONED,
            MissionState.SUPERSEDED,
        }
    ),
    MissionState.PAUSED: frozenset(
        {
            MissionState.ACTIVE,
            MissionState.BLOCKED,
            MissionState.ABANDONED,
            MissionState.SUPERSEDED,
        }
    ),
    MissionState.BLOCKED: frozenset(
        {
            MissionState.ACTIVE,
            MissionState.PAUSED,
            MissionState.ABANDONED,
            MissionState.SUPERSEDED,
        }
    ),
    MissionState.COMPLETED: frozenset({MissionState.SUPERSEDED}),
    MissionState.ABANDONED: frozenset({MissionState.SUPERSEDED}),
    MissionState.SUPERSEDED: frozenset(),
}

TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.TODO: frozenset(
        {TaskState.IN_PROGRESS, TaskState.BLOCKED, TaskState.DONE, TaskState.CANCELLED}
    ),
    TaskState.IN_PROGRESS: frozenset(
        {TaskState.TODO, TaskState.BLOCKED, TaskState.DONE, TaskState.CANCELLED}
    ),
    TaskState.BLOCKED: frozenset(
        {TaskState.TODO, TaskState.IN_PROGRESS, TaskState.DONE, TaskState.CANCELLED}
    ),
    TaskState.DONE: frozenset(),
    TaskState.CANCELLED: frozenset(),
}

FEATURE_TRANSITIONS: dict[FeatureState, frozenset[FeatureState]] = {
    FeatureState.PROPOSED: frozenset(
        {
            FeatureState.PLANNED,
            FeatureState.IN_PROGRESS,
            FeatureState.PRESENT,
            FeatureState.REJECTED,
        }
    ),
    FeatureState.PLANNED: frozenset(
        {FeatureState.IN_PROGRESS, FeatureState.PRESENT, FeatureState.REJECTED}
    ),
    FeatureState.IN_PROGRESS: frozenset(
        {FeatureState.PRESENT, FeatureState.REJECTED, FeatureState.PLANNED}
    ),
    FeatureState.PRESENT: frozenset({FeatureState.DEPRECATED, FeatureState.REMOVED}),
    FeatureState.DEPRECATED: frozenset({FeatureState.REMOVED, FeatureState.PRESENT}),
    FeatureState.REMOVED: frozenset(),
    FeatureState.REJECTED: frozenset(),
}

# Census classifications that become Clank identities on import.
REGISTERABLE_CLASSIFICATIONS = frozenset(
    {
        CensusClassification.VERIFIED,
        CensusClassification.PROBABLE,
        CensusClassification.NEEDS_RECONSTRUCTION,
        CensusClassification.SUPPORT_COMPONENT,
    }
)
