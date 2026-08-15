"""Append-only reward labels kept beside, but not rewritten into, trace facts.

Environment observations are facts produced during execution. Rewards are labels often
produced later by an evaluator that may itself change. Keeping their history in a sidecar
lets a trajectory be rebuilt with a named evaluator without pretending a late score was
known when the original trace was written.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from agentevolver.utils import make_id


REWARD_LABEL_SCHEMA_VERSION = 1


class UnsupportedRewardLabel(ValueError):
    """A label schema is newer than the reader and cannot be safely reinterpreted."""


class RewardLabel(BaseModel):
    """One immutable evaluation of a task-level trajectory."""

    schema_version: int = REWARD_LABEL_SCHEMA_VERSION
    label_id: str = Field(default_factory=make_id)
    session_id: str
    task_id: str
    reward: float
    granularity: str = "task"
    evaluator: str = "external"
    evaluator_version: Optional[str] = None
    source_trace_seq_start: Optional[int] = None
    source_trace_seq_end: Optional[int] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = [
    "REWARD_LABEL_SCHEMA_VERSION",
    "UnsupportedRewardLabel",
    "RewardLabel",
]
