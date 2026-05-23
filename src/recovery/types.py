"""Recovery recipe type definitions.

Port of recovery_recipes.rs from claw-code reference implementation.
Each FailureScenario maps to a RecoveryRecipe with ordered steps and retry limits.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class FailureScenario(str, Enum):
    """Known failure modes that have deterministic recovery procedures."""
    TOOL_TIMEOUT        = "tool_timeout"
    PERMISSION_DENIED   = "permission_denied"
    CONTEXT_OVERFLOW    = "context_overflow"
    STALE_BRANCH        = "stale_branch"
    PARSE_ERROR         = "parse_error"
    MAX_STEPS_EXCEEDED  = "max_steps_exceeded"
    LLM_ERROR           = "llm_error"
    UNKNOWN             = "unknown"


class RecoveryStep(BaseModel):
    """A single action step within a recovery recipe."""
    description: str = Field(description="Human-readable description of this step.")
    instruction: str = Field(description="Instruction to inject into the agent context.")


class RecoveryRecipe(BaseModel):
    """A deterministic recovery procedure for a specific failure scenario."""
    scenario: FailureScenario
    max_attempts: int = Field(default=1, description="Max retries before escalation.")
    steps: List[RecoveryStep] = Field(default_factory=list)
    escalate_on_exhaust: bool = Field(default=True)


class RecoveryEvent(BaseModel):
    """Immutable record of a recovery attempt."""
    session_id: str
    scenario: FailureScenario
    attempt_number: int
    step_index: int
    instruction: str
    recovered: bool = False
