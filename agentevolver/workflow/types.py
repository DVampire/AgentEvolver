"""Contracts for compiled, HTML-authored dynamic workflows."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class WorkflowState(str, Enum):
    CREATED = "created"
    VALIDATING = "validating"
    REJECTED = "rejected"
    READY = "ready"
    PENDING = "pending"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    RESUMING = "resuming"
    CANCELLING = "cancelling"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStatus(str, Enum):
    EPHEMERAL = "ephemeral"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class StepType(str, Enum):
    AGENT = "agent"
    TOOL = "tool"
    SKILL = "skill"
    CONNECTOR = "connector"
    WORKFLOW = "workflow"
    PARALLEL = "parallel"
    MAP = "map"
    REDUCE = "reduce"
    BRANCH = "branch"
    LOOP = "loop"
    VERIFY = "verify"
    CHECKPOINT = "checkpoint"


class ExecutionState(str, Enum):
    """Lifecycle of one dynamic execution frame."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    CACHED = "cached"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class InvocationState(str, Enum):
    """Lifecycle of a concrete capability invocation."""

    QUEUED = "queued"
    ACQUIRING_SLOT = "acquiring_slot"
    STARTING = "starting"
    RUNNING = "running"
    WAITING_PERMISSION = "waiting_permission"
    WAITING_INPUT = "waiting_input"
    RETRYING = "retrying"
    CACHED = "cached"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowInput(BaseModel):
    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""


class WorkflowStep(BaseModel):
    """Safe internal instruction emitted by WorkflowCompiler."""

    model_config = ConfigDict(extra="forbid")
    type: StepType
    id: str
    target: Optional[str] = None
    task: str = ""
    args: Dict[str, Any] = Field(default_factory=dict)
    items: Any = None
    item_name: str = "item"
    condition: Any = None
    condition_mode: str = "until"
    concurrency: Optional[int] = None
    max_rounds: Optional[int] = None
    no_progress_limit: int = 0
    retries: int = 0
    timeout: Optional[float] = None
    min_votes: int = 1
    children: List["WorkflowStep"] = Field(default_factory=list)
    else_children: List["WorkflowStep"] = Field(default_factory=list)


class WorkflowDefinition(BaseModel):
    """Compiled workflow plus searchable metadata and immutable HTML source."""

    name: str
    schema_version: str = "1.0.0"
    description: str = ""
    version: str = "1.0.0"
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    tags: List[str] = Field(default_factory=list)
    applicability: str = ""
    inputs: Dict[str, WorkflowInput] = Field(default_factory=dict)
    program: List[WorkflowStep]
    outputs: Dict[str, Any] = Field(default_factory=dict)
    max_agents: int = Field(default=1000, ge=1, le=1000)
    max_concurrency: int = Field(default=16, ge=1, le=64)
    max_depth: int = Field(default=8, ge=1, le=32)
    timeout: Optional[float] = Field(default=None, gt=0)
    enable_evolving: bool = False
    source: str = ""
    source_path: Optional[str] = None


class ExecutionFrame(BaseModel):
    """One runtime expansion of a static step (map item, loop round, branch, etc.)."""

    key: str
    parent_key: Optional[str] = None
    step_id: str
    step_type: StepType
    state: ExecutionState = ExecutionState.PENDING
    item_index: Optional[int] = None
    iteration: Optional[int] = None
    scope: Dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class InvocationAttempt(BaseModel):
    number: int
    state: InvocationState = InvocationState.QUEUED
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class InvocationRun(BaseModel):
    """A concrete Agent/Tool/Skill/Connector invocation and its retry attempts."""

    key: str
    frame_key: str
    capability_type: StepType
    capability_name: str
    state: InvocationState = InvocationState.QUEUED
    input: Dict[str, Any] = Field(default_factory=dict)
    attempts: List[InvocationAttempt] = Field(default_factory=list)
    output: Any = None
    error: Optional[str] = None
    cached: bool = False
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class WorkflowRun(BaseModel):
    runtime_version: str = "1.0.0"
    id: str
    workflow_name: str
    workflow_version: str
    state: WorkflowState = WorkflowState.CREATED
    input: Dict[str, Any] = Field(default_factory=dict)
    variables: Dict[str, Any] = Field(default_factory=dict)
    frames: Dict[str, ExecutionFrame] = Field(default_factory=dict)
    invocations: Dict[str, InvocationRun] = Field(default_factory=dict)
    output: Any = None
    error: Optional[str] = None
    agent_count: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    checkpoint_path: Optional[str] = None
    paused_at: Optional[str] = None

    @property
    def successful(self) -> bool:
        return self.state == WorkflowState.SUCCEEDED


class WorkflowEvaluation(BaseModel):
    """Version-scoped evidence used to retain, improve, or roll back a workflow."""

    workflow_name: str
    workflow_version: str
    run_id: Optional[str] = None
    success: bool
    quality_score: float = Field(ge=0.0, le=1.0)
    token_cost: int = Field(default=0, ge=0)
    elapsed_ms: float = Field(default=0.0, ge=0.0)
    notes: str = ""


__all__ = [
    "ExecutionFrame", "ExecutionState", "InvocationAttempt", "InvocationRun",
    "InvocationState", "StepType", "WorkflowDefinition", "WorkflowInput",
    "WorkflowEvaluation", "WorkflowRun", "WorkflowState", "WorkflowStatus", "WorkflowStep",
]
