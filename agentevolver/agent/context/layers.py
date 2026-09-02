"""The four layers, and a message list that remembers what each one cost.

The order is the contract: everything stable enough to cache comes first, everything
that changes every step comes last. A provider's prompt cache is a prefix match, so a
single volatile block placed early throws away the whole session's cached tokens.

fixed        system prompt and the task anchor - written once, never rewritten
checkpoint   the one canonical compaction summary, when history has been folded
recent       the exact assistant/tool turns still being replayed
live         this step's volatile state: budgets, errors, plan mode, reminders
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

from agentevolver.message.types import Message

#: Layer order. Also the order :meth:`ContextEnvelope.flatten` emits.
LAYERS: Tuple[str, ...] = ("fixed", "checkpoint", "recent", "live")

#: Historical spelling kept for in-tree callers that import the private name.
_LAYERS = LAYERS


class ContextMessages(list[Message]):
    """A message list retaining validated per-layer token accounting."""

    def __init__(self, messages: Iterable[Message], layer_tokens: Dict[str, int]):
        super().__init__(messages)
        self.layer_tokens = dict(layer_tokens)


__all__ = ["LAYERS", "ContextMessages"]
