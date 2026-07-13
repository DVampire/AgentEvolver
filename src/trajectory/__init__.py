"""Trajectory — step-level, reward-annotated training-data capture.

A projection of one agent run into a trainable form: ``trajectory_manager``
accumulates steps from the agent lifecycle (via ``TrajectoryHook``), persists
them as JSONL, and exports SFT / RL records.
"""

from src.trajectory.types import Trajectory, TrajectoryContext, TrajectoryStep, RLFormat
from src.trajectory.server import trajectory_manager, TrajectoryManagerServer
from src.trajectory.default import VerlFormat

__all__ = [
    "Trajectory",
    "TrajectoryContext",
    "TrajectoryStep",
    "RLFormat",
    "trajectory_manager",
    "TrajectoryManagerServer",
    "VerlFormat",
]
