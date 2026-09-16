"""The validated four-layer envelope.

Validation is the point. A context that looks fine and is subtly wrong - an assistant
turn whose tool results never arrived, a compaction summary sitting in the wrong layer -
is rejected by the provider on the next request, far from the code that built it. Every
rule here turns one of those into an error at the moment it is constructed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from agentevolver.agent.context.errors import ContextProtocolError
from agentevolver.agent.context.layers import LAYERS as _LAYERS
from agentevolver.agent.context.layers import ContextMessages
from agentevolver.message.types import (
    AssistantMessage,
    CompactionMessage,
    Message,
    ToolMessage,
)


@dataclass(frozen=True)
class ContextEnvelope:
    """Validated fixed → checkpoint → recent → live provider-neutral context."""

    fixed: Tuple[Message, ...] = field(default_factory=tuple)
    checkpoint: Tuple[CompactionMessage, ...] = field(default_factory=tuple)
    recent: Tuple[Message, ...] = field(default_factory=tuple)
    live: Tuple[Message, ...] = field(default_factory=tuple)

    def validate(self) -> "ContextEnvelope":
        if len(self.checkpoint) > 1:
            raise ContextProtocolError("context may contain only one canonical checkpoint")
        if any(not isinstance(message, CompactionMessage) for message in self.checkpoint):
            raise ContextProtocolError("checkpoint layer accepts only CompactionMessage")
        other = (*self.fixed, *self.recent, *self.live)
        if any(isinstance(message, CompactionMessage) for message in other):
            raise ContextProtocolError("CompactionMessage must live only in checkpoint")
        if any(message.role not in {"system", "user"} for message in self.fixed):
            raise ContextProtocolError("fixed layer accepts only system/task messages")
        if any(message.role != "user" for message in self.live):
            raise ContextProtocolError("live layer accepts only current user context")
        if any(message.role == "system" for message in self.recent):
            raise ContextProtocolError("system messages must remain in the fixed layer")

        seen_user = False
        for message in self.fixed:
            if message.role == "user":
                seen_user = True
            elif seen_user:
                raise ContextProtocolError("fixed system messages must precede the task anchor")
        all_messages = (*self.fixed, *self.checkpoint, *self.recent, *self.live)
        if len({id(message) for message in all_messages}) != len(all_messages):
            raise ContextProtocolError("one message cannot belong to multiple context layers")
        self._validate_tool_turns()
        return self

    def _validate_tool_turns(self) -> None:
        pending: set[str] = set()
        seen: set[str] = set()
        previous_assistant = False
        for message in self.recent:
            if isinstance(message, AssistantMessage):
                if pending:
                    raise ContextProtocolError(
                        f"assistant turn started before tool results arrived: {sorted(pending)}"
                    )
                if previous_assistant:
                    raise ContextProtocolError(
                        "adjacent assistant turns cannot preserve provider-owned reasoning state"
                    )
                ids = [str(call.id) for call in message.tool_calls]
                if len(ids) != len(set(ids)) or any(call_id in seen for call_id in ids):
                    raise ContextProtocolError("tool-call ids must be unique in the exact tail")
                pending.update(ids)
                seen.update(ids)
                previous_assistant = True
            elif isinstance(message, ToolMessage):
                call_id = str(message.tool_call_id)
                if call_id not in pending:
                    raise ContextProtocolError(f"orphan tool result: {call_id}")
                pending.remove(call_id)
                previous_assistant = False
            elif pending:
                raise ContextProtocolError(
                    f"tool results must immediately follow their assistant turn: {sorted(pending)}"
                )
            else:
                previous_assistant = False
        if pending:
            raise ContextProtocolError(f"tool turn is incomplete: {sorted(pending)}")

    def token_counts(self) -> Dict[str, int]:
        from agentevolver.model.pressure import estimate_tokens

        return {
            layer: estimate_tokens(list(getattr(self, layer))) if getattr(self, layer) else 0
            for layer in _LAYERS
        }

    def flatten(self) -> ContextMessages:
        self.validate()
        messages: List[Message] = []
        for layer in _LAYERS:
            messages.extend(
                message.model_copy(update={"context_layer": layer})
                for message in getattr(self, layer)
            )
        return ContextMessages(messages, self.token_counts())


__all__ = ["ContextEnvelope"]
