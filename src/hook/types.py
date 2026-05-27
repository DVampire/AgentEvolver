"""Hook types — HookEvent, HookContext, HookResult, and Hook base class."""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.message import Message
from src.session import BaseContext


class HookEvent(str, Enum):
    """Lifecycle events that middleware can intercept."""

    # Message pipeline — fires inside _get_messages before returning to agent
    PRE_MESSAGES = "pre_messages"

    # Action lifecycle — fires around each action in _think_and_act
    PRE_ACTION = "pre_action"
    POST_ACTION = "post_action"

    # Step lifecycle — fires around each full agent step
    PRE_STEP = "pre_step"
    POST_STEP = "post_step"

    # Agent lifecycle
    ON_START = "on_start"
    ON_STOP = "on_stop"       # agent is about to call done_tool
    ON_ESCALATE = "on_escalate"  # agent is blocked and requests Meta guidance
    ON_CALL = "on_call"


class HookContext(BaseContext):
    """Context passed into hook manager and individual hook handlers.

    Event-specific payload lives in ``extra``:
      - extra["event"]         → HookEvent
      - extra["messages"]      → List[Message]  (PRE_MESSAGES)
      - extra["action"]        → dict            (PRE_ACTION / POST_ACTION)
      - extra["action_result"] → str             (POST_ACTION)
      - extra["step_number"]   → int             (PRE_STEP / POST_STEP)
      - extra["max_tokens"]    → int             (PRE_MESSAGES)
    ``name`` holds the agent name that fired the event.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    id: str = Field(description="Session ID or any unique identifier for this hook context.")
    name: str = Field(description="Hook name that fired the event.")
    timeout: Optional[float] = Field(default=3600, description="Optional timeout for this context. Defaults to 1 hour.")
    work_dir: Optional[str] = Field(default=None, description="Optional working directory for this context.")
    extra: Optional[Dict[str, Any]] = Field(default=None, description="Event-specific payload.")


class HookDecision(str, Enum):
    """What the middleware wants to happen next."""
    ALLOW = "allow"       # continue normally
    BLOCK = "block"       # stop this action / step
    MODIFY = "modify"     # use modified_messages / modified_action


class HookResult(BaseModel):
    """What a middleware handler returns."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    decision: HookDecision = Field(default=HookDecision.ALLOW)
    reason: Optional[str] = Field(default=None, description="Human-readable reason (for BLOCK).")

    # Modified data — only used when decision == MODIFY
    modified_messages: Optional[List[Message]] = Field(default=None)
    modified_action: Optional[Dict[str, Any]] = Field(default=None)

    # Extra context to inject into the next agent message (like Claude Code additionalContext)
    additional_context: Optional[str] = Field(default=None)

    @classmethod
    def allow(cls) -> "HookResult":
        return cls(decision=HookDecision.ALLOW)

    @classmethod
    def block(cls, reason: str = "") -> "HookResult":
        return cls(decision=HookDecision.BLOCK, reason=reason)

    @classmethod
    def modify_messages(cls, messages: List[Message], additional_context: str = "") -> "HookResult":
        return cls(
            decision=HookDecision.MODIFY,
            modified_messages=messages,
            additional_context=additional_context or None,
        )

    @classmethod
    def modify_action(cls, action: Dict[str, Any]) -> "HookResult":
        return cls(decision=HookDecision.MODIFY, modified_action=action)


def _merge_results(results: List[HookResult]) -> HookResult:
    """Merge results from parallel middleware handlers.

    Most restrictive decision wins (BLOCK > MODIFY > ALLOW).
    All additional_context strings are concatenated.
    For MODIFY, the last non-None modified_messages / modified_action wins.
    """
    if not results:
        return HookResult.allow()

    final_decision = HookDecision.ALLOW
    final_reason = None
    final_messages = None
    final_action = None
    context_parts: List[str] = []

    for r in results:
        if r.decision == HookDecision.BLOCK:
            final_decision = HookDecision.BLOCK
            if r.reason:
                final_reason = r.reason
        elif r.decision == HookDecision.MODIFY and final_decision != HookDecision.BLOCK:
            final_decision = HookDecision.MODIFY
            if r.modified_messages is not None:
                final_messages = r.modified_messages
            if r.modified_action is not None:
                final_action = r.modified_action

        if r.additional_context:
            context_parts.append(r.additional_context)

    return HookResult(
        decision=final_decision,
        reason=final_reason,
        modified_messages=final_messages,
        modified_action=final_action,
        additional_context="\n\n".join(context_parts) if context_parts else None,
    )


class Hook(BaseModel):
    """Base class for all hook handlers."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="Unique name for this hook.")
    description: str = Field(default="", description="What this hook does.")
    enabled: bool = Field(default=True)
    # Execution priority — lower number runs first.
    priority: int = Field(default=100)

    async def handle(self, ctx: HookContext) -> HookResult:
        """Override this method to implement hook logic."""
        return HookResult.allow()

    async def cleanup(self, session_id: str) -> None:
        """Called when a session ends (ON_STOP). Override to release per-session state."""
