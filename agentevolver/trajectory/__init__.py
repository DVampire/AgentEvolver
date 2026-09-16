"""Trajectory — step-level, reward-annotated training-data capture.

A projection of one agent run into a trainable form: ``trajectory_manager``
accumulates steps from the agent lifecycle (via ``TrajectoryHook``), persists
them as JSONL, and exports SFT / RL records.
"""

from agentevolver.trajectory.types import (
    RL_EXPORT_VERSION,
    SFT_EXPORT_VERSION,
    TRAJECTORY_SCHEMA_VERSION,
    RLFormat,
    Trajectory,
    TrajectoryContext,
    TrajectoryStep,
)
from agentevolver.trajectory.server import trajectory_manager, TrajectoryManagerServer
from agentevolver.trajectory.default import VerlFormat
from agentevolver.trajectory.labels import (
    REWARD_LABEL_SCHEMA_VERSION,
    RewardLabel,
    UnsupportedRewardLabel,
)
from agentevolver.trajectory.projector import (
    PROJECTOR_VERSION,
    PROJECTION_NAME,
    PROJECTION_STATE_VERSION,
    IncrementalTrajectoryProjector,
    TrajectoryProjectionState,
    project_trajectory,
)

__all__ = [
    "Trajectory",
    "TrajectoryContext",
    "TrajectoryStep",
    "RLFormat",
    "TRAJECTORY_SCHEMA_VERSION",
    "SFT_EXPORT_VERSION",
    "RL_EXPORT_VERSION",
    "REWARD_LABEL_SCHEMA_VERSION",
    "RewardLabel",
    "UnsupportedRewardLabel",
    "PROJECTOR_VERSION",
    "PROJECTION_NAME",
    "PROJECTION_STATE_VERSION",
    "IncrementalTrajectoryProjector",
    "TrajectoryProjectionState",
    "project_trajectory",
    "trajectory_manager",
    "TrajectoryManagerServer",
    "VerlFormat",
]
