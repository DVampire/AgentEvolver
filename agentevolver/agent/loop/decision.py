"""What one model turn produced, and what one action returned.

Two plain records with no behaviour beyond converting themselves into messages. They
exist so the loop reads as ``think → act`` over typed values rather than over a
dictionary whose keys each step has to remember.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agentevolver.message.types import AssistantMessage, Function, ToolCall, ToolMessage


@dataclass(frozen=True)
class ActionCall:
    """One tool call the model asked for."""

    id: str
    name: str
    args: Dict[str, Any] = field(default_factory=dict)
    caller: Optional[Dict[str, Any]] = None

    def as_tool_call(self) -> ToolCall:
        return ToolCall(
            id=self.id,
            caller=self.caller,
            function=Function(
                name=self.name, arguments=json.dumps(self.args, ensure_ascii=False)
            ),
        )

    def signature(self) -> str:
        """Stable identity of this call, for repeat detection."""
        return json.dumps(
            {"name": self.name, "args": self.args},
            sort_keys=True, ensure_ascii=False, default=str,
        )


@dataclass
class Decision:
    """One model turn: what it said, what it wants to run, and how it stopped."""

    text: str = ""
    reasoning: str = ""
    calls: List[ActionCall] = field(default_factory=list)
    #: Opaque provider items (signed thinking, reasoning ids) required on replay.
    provider_state: Dict[str, Any] = field(default_factory=dict)
    usage: Optional[Dict[str, Any]] = None
    stop_reason: str = ""
    #: Set when the call itself failed. A decision with an error is not a turn.
    error: str = ""
    #: The request was refused for length before it was sent. Separated from an ordinary
    #: error because the answer is different: retrying the same prompt cannot succeed,
    #: but folding history and rebuilding it can.
    overflowed: bool = False

    @property
    def truncated(self) -> bool:
        """The provider stopped at its output limit before the call was complete.

        Such a turn is discarded rather than dispatched: arguments assembled from a
        truncated stream are syntactically plausible and semantically wrong, and running
        them turns a transport failure into a bogus tool error.
        """
        return self.stop_reason == "max_tokens"

    @property
    def final(self) -> bool:
        """The model answered instead of acting — this run is done.

        No ``done_tool`` required. A turn with no tool call is an answer, which is what
        every provider's ``end_turn`` already means; demanding a special tool to say so
        cost a step and made a text reply look like a protocol violation.
        """
        return not self.calls and not self.error and not self.truncated

    def as_assistant(self) -> AssistantMessage:
        """The message to append to the conversation for this turn."""
        return AssistantMessage(
            content=self.text,
            tool_calls=[call.as_tool_call() for call in self.calls],
            provider_state=dict(self.provider_state),
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        names = ",".join(call.name for call in self.calls) or "-"
        return f"Decision(calls=[{names}], stop={self.stop_reason or 'ok'})"


@dataclass(frozen=True)
class ActionResult:
    """What one action returned."""

    call: ActionCall
    output: str = ""
    error: str = ""
    #: The capability declared this run complete (an explicit finish tool).
    final: bool = False
    #: Anything the capability wants carried alongside — files, ids, a child's pid.
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error

    def as_message(self) -> ToolMessage:
        """The tool result message answering this call."""
        return ToolMessage(
            content=self.error or self.output or "(no output)",
            tool_call_id=self.call.id,
            caller=self.call.caller,
            name=self.call.name,
            is_error=bool(self.error),
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ActionResult({self.call.name}, {'error' if self.error else 'ok'})"


__all__ = ["ActionCall", "ActionResult", "Decision"]
