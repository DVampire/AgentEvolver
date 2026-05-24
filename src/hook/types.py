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

    # Action lifecycle — fires around each action in _think_and_action
    PRE_ACTION = "pre_action"
    POST_ACTION = "post_action"

    # Step lifecycle — fires around each full agent step
    PRE_STEP = "pre_step"
    POST_STEP = "post_step"

    # Agent lifecycle
    ON_START = "on_start"
    ON_STOP = "on_stop"       # agent is about to call done_tool
    ON_ESCALATE = "on_escalate"  # agent is blocked and requests Meta guidance
    ON_CUSTOM = "on_custom"   # arbitrary structured data emitted by the agent (e.g. plan state updates)


class HookContext(BaseContext):
    """Immutable snapshot of agent state passed to every middleware handler."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    event: HookEvent = Field(description="The event that triggered this hook.")
    agent_name: str = Field(default="", description="Name of the agent firing the event.")

    # Present for PRE_MESSAGES / POST_MESSAGES
    messages: Optional[List[Message]] = Field(default=None)

    # Present for PRE_ACTION / POST_ACTION
    action: Optional[Dict[str, Any]] = Field(default=None, description="The action dict being executed.")
    action_result: Optional[str] = Field(default=None, description="Result of the action (POST_ACTION only).")

    # Present for PRE_STEP / POST_STEP
    step_number: Optional[int] = Field(default=None)

    # Agent-level metadata
    max_tokens: Optional[int] = Field(default=None)
    extra: Optional[Dict[str, Any]] = Field(default_factory=dict)


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
    # Which events this hook handles. Empty = handle all.
    events: List[HookEvent] = Field(default_factory=list)
    # Execution priority — lower number runs first.
    priority: int = Field(default=100)

    async def handle(self, ctx: HookContext) -> HookResult:
        """Override this method to implement hook logic."""
        return HookResult.allow()

    def handles_event(self, event: HookEvent) -> bool:
        return self.enabled and (not self.events or event in self.events)
