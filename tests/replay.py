"""Drive the real agent loop against a recorded model, with no API key.

Not a test module — a harness the tests import.

The loop is this repository's largest untested surface, and the reason is not that nobody
tried: every path through it needs a model, so every test of it needs a key, a network, and
a model that answers the same way twice. None of those are available in CI, so the loop is
exercised only by running the product.

The way out is the one deepseek-harness takes: a **replay model**. Their scripts are derived
from a recorded session's own log, which works because their rule is "model-visible means
logged". Ours records less — no token-level chunks — but it records the part that decides
what the loop does: each step's reasoning, and the tool calls it made with their arguments.
That is enough to replay a run's *decisions*, which is what testing a loop is about.

So a script here is a list of steps, and a step is what the model decided:

    Step(reasoning="I should look at the file", tool_calls=[Call("read", {"path": "x"})])

Feed it to `replaying()` and the real `Agent` runs against it — real tool dispatch, real
hooks, real trace, real memory — with only the provider replaced.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence
from unittest.mock import patch

from agentevolver.model.types import (
    StreamDone,
    ThinkingDelta,
    ToolCallComplete,
)


@dataclass
class Call:
    """One tool call a recorded step made."""

    name: str
    args: Dict[str, Any] = field(default_factory=dict)
    id: str = ""


@dataclass
class Step:
    """What the model decided on one step.

    `usage` is carried because the loop reads it — a step with no usage records zero
    tokens, and a test about token accounting driven by a script that omits it would be
    measuring the harness.
    """

    reasoning: str = ""
    tool_calls: List[Call] = field(default_factory=list)
    usage: Optional[Dict[str, Any]] = None
    stop_reason: str = "stop"


class ScriptExhausted(RuntimeError):
    """The loop asked for one more step than the script has.

    Raised rather than looping the last step or returning an empty one. A silent extra
    step is the failure mode that makes a replay test meaningless: the loop runs away, the
    script says nothing about it, and the test passes because nothing asserted a count.
    """


class ReplayModel:
    """Yields a recorded decision per call, in order."""

    def __init__(self, script: Sequence[Step]):
        self.script = list(script)
        self.calls: List[Dict[str, Any]] = []  # what the loop actually sent

    @property
    def consumed(self) -> int:
        return len(self.calls)

    def stream(
        self,
        name: Optional[str] = None,
        input: Optional[Dict[str, Any]] = None,
        ctx: Any = None,
        **kwargs: Any,
    ):
        """Stand in for `model_manager.stream`, returning the next step as an event stream.

        The request is retained before anything is yielded, so a test can assert what the
        loop *sent* — the assembled messages and tool schemas — which is usually the thing
        under test when the loop itself is the subject.
        """
        index = len(self.calls)
        self.calls.append({"name": name, "input": input or {}, "ctx": ctx})
        if index >= len(self.script):
            raise ScriptExhausted(
                f"the loop asked for step {index + 1}; the script has {len(self.script)}"
            )
        return _events(self.script[index])

    def bound(self):
        """`stream` as the manager's own method, with the instance argument absorbed.

        The patch replaces the attribute on the *class*: `ModelManagerServer` is a pydantic
        model, and pydantic refuses an attribute the model does not declare, so patching
        the singleton instance raises instead of taking effect.
        """

        def _stream(_manager_self, *args, **kwargs):
            return self.stream(*args, **kwargs)

        return _stream


async def _events(step: Step):
    """One step as the canonical event stream a provider would produce."""
    if step.reasoning:
        yield ThinkingDelta(text=step.reasoning)
    for index, call in enumerate(step.tool_calls):
        yield ToolCallComplete(
            index=index,
            id=call.id or f"call_{index}",
            name=call.name,
            input=call.args,
        )
    yield StreamDone(stop_reason=step.stop_reason, usage=step.usage)


@contextmanager
def replaying(script: Sequence[Step]) -> Iterator[ReplayModel]:
    """Replace the model for the duration of the block.

    Patched at `model_manager.stream` rather than at a registered provider: the loop
    resolves a model by name through the manager, so a fake provider would also need a
    registered `ModelConfig` and a client factory — three pieces of setup that test the
    registry rather than the loop.
    """
    from agentevolver.model import model_manager

    model = ReplayModel(script)
    with patch.object(type(model_manager), "stream", model.bound()):
        yield model


# --------------------------------------------------------------------------- #
# Building a script from a recorded run
# --------------------------------------------------------------------------- #
def script_from_events(events: Sequence[Any]) -> List[Step]:
    """Recover the decisions a recorded run made, in step order.

    Each `agent_call` event carries that step's reasoning; the `tool_start` events sharing
    its `step_number` carry the calls it made and their arguments. `trace/derive.py` already
    joins these two to rebuild an assistant turn — this is the same join, kept separate
    because that one produces messages for a model and this one produces a script for a
    test.

    A run recorded before a field existed replays as far as it can: a missing reasoning is
    an empty string, not a dropped step.
    """
    from agentevolver.trace.types import TraceEventType

    calls_by_step: Dict[int, List[Any]] = {}
    for event in events:
        if event.event_type == TraceEventType.TOOL_START:
            calls_by_step.setdefault(getattr(event, "step_number", 0) or 0, []).append(event)

    steps: List[Step] = []
    for event in events:
        if event.event_type != TraceEventType.AGENT_CALL:
            continue
        number = getattr(event, "step_number", 0) or 0
        steps.append(
            Step(
                reasoning=getattr(event, "reasoning", None)
                or getattr(event, "message", None)
                or "",
                tool_calls=[
                    Call(name=start.action_name or "", args=start.input or {})
                    for start in calls_by_step.get(number, [])
                ],
            )
        )
    return steps


def script_from_jsonl(path: str) -> List[Step]:
    """Read a recorded session's trace file and recover its script."""
    from agentevolver.trace.types import TraceEvent

    events = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                events.append(TraceEvent(**json.loads(line)))
            except (ValueError, TypeError):
                # A truncated tail is the normal shape of a log from a killed run; losing
                # the whole script over its last line would discard every step before it.
                continue
    return script_from_events(events)


__all__ = [
    "Call",
    "ReplayModel",
    "ScriptExhausted",
    "Step",
    "replaying",
    "script_from_events",
    "script_from_jsonl",
]
