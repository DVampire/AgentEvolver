"""Project a session's log into the message history a model would be sent.

`Agent._derived_messages` calls this on the request path when `derive_context` is on,
which is off by default. The switch is opt-in because turning it on changes what every
step of every agent sees, and the first run with it on failed on every step after the
first while reporting success: no serializer knew `ToolMessage`, this projection put each
step's results *before* the assistant turn that produced them, and `agent_end` hardcoded
`success=True` so nothing could contradict the measurement.

What it produces is the shape the model was trained on:

    user       the task
    assistant  the step's reasoning, carrying its tool calls
    tool       one result per call, matched by id
    ...
    assistant  the final result

against what the log stands for today: prose lines describing results, re-rendered
into one blob every step.

Two readings of the log meet here. The **surface** (see `surface.py`) decides which
history events still stand — a compaction summary shadows the range it replaced, so
this yields the summary and not the events behind it. The **log-only** events supply
what the surface deliberately omits: `tool_start` carries the arguments of a call,
which are part of the assistant's turn rather than a message of their own.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from agentevolver.message.types import (
    AssistantMessage,
    Function,
    HumanMessage,
    Message,
    ToolCall,
    ToolMessage,
)
from agentevolver.trace.surface import fold_surface
from agentevolver.trace.types import TraceEventType


def _call_id(event: Any) -> str:
    """The model's id for a call, falling back to position.

    Events written before `call_id` existed have only `(step, index)` to identify a
    call by. The fallback keeps those logs projectable instead of dropping their
    results, at the cost of an id that means nothing outside this session.
    """
    meta = getattr(event, "metadata", None) or {}
    explicit = meta.get("call_id")
    if explicit:
        return str(explicit)
    return f"step{getattr(event, 'step_number', 0)}_call{getattr(event, 'action_index', 0)}"


def _tool_call(start: Any) -> ToolCall:
    """One logged call as the message layer expresses it.

    The message layer speaks the provider wire shape — arguments as a JSON string
    inside a `function` — while the log stores them parsed. The conversion belongs
    here rather than in the log, which should keep the readable form.
    """
    return ToolCall(
        id=_call_id(start),
        function=Function(
            name=start.action_name or "",
            arguments=json.dumps(start.input or {}, ensure_ascii=False, sort_keys=True, default=str),
        ),
    )


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def derive_messages(events: Sequence[Any]) -> List[Message]:
    """Project ``events`` into the model-visible history.

    Args:
        events: One session's log, in write order.

    Returns:
        The messages a request would carry, without the system prompt — that is
        rendered from a template and is not part of the log.

    Raises:
        SurfaceError: The log's surface declarations do not hold up. Refusing beats
            projecting a history the log does not actually support.
    """
    on_surface = set(fold_surface(events)["nodes"])

    # The call side, keyed by id. These events are log-only: a call is part of the
    # assistant's turn, not a message of its own, so it never joins the surface.
    calls_by_step: Dict[int, List[Any]] = {}
    for event in events:
        if event.event_type in (TraceEventType.TOOL_START, TraceEventType.SKILL_START):
            calls_by_step.setdefault(getattr(event, "step_number", 0) or 0, []).append(event)

    messages: List[Message] = []
    pending: Optional[List[Any]] = None      # results waiting for their assistant turn

    def flush_results() -> None:
        nonlocal pending
        for result in pending or []:
            messages.append(ToolMessage(
                content=_text(result.message if result.success else (result.error or result.message)),
                tool_call_id=_call_id(result),
                name=getattr(result, "action_name", None),
                is_error=not bool(result.success),
            ))
        pending = None

    for event in events:
        seq = getattr(event, "seq_no", None)
        kind = event.event_type

        if seq not in on_surface:
            continue

        if kind == TraceEventType.AGENT_CALL:
            # The assistant's turn: the step's reasoning, plus the calls it made. The
            # calls come from log-only `*_start` events — a call is part of this turn,
            # not a message of its own — so they are joined in here rather than
            # projected separately.
            #
            # The assistant goes in *before* its results. The log records the step's
            # tool events first and its AGENT_CALL last, because the call event is
            # written when the step closes — so `pending` here holds this step's
            # results, not the previous step's. Flushing first inverted every turn:
            # `[user, tool, tool, assistant]`, which a provider rejects outright
            # ("each tool_result must have a corresponding tool_use in the previous
            # message") and which failed every step after the first.
            step = getattr(event, "step_number", 0) or 0
            messages.append(AssistantMessage(
                content=_text(event.reasoning or event.message),
                tool_calls=[_tool_call(s) for s in calls_by_step.get(step, [])],
            ))
            flush_results()
        elif kind == TraceEventType.AGENT_START:
            flush_results()
            messages.append(HumanMessage(content=_text((event.input or {}).get("task"))))
        elif kind in (TraceEventType.TOOL_CALL, TraceEventType.SKILL_CALL):
            (pending := pending if pending is not None else []).append(event)
        elif kind == TraceEventType.AGENT_END:
            flush_results()
            messages.append(AssistantMessage(content=_text(event.output or event.message)))
        elif kind == TraceEventType.CUSTOM and (event.metadata or {}).get("kind") == "compaction":
            # A summary stands where its range used to. It rides as a user turn for the
            # same reason dsh does it: only message-producing roles reach the model, and
            # a summary is context handed to the assistant, not something it said.
            flush_results()
            messages.append(HumanMessage(content=_text(event.message)))
        elif kind == TraceEventType.ERROR:
            flush_results()
            messages.append(HumanMessage(content=f"Error: {_text(event.error or event.message)}"))

    flush_results()
    return messages


__all__ = ["derive_messages"]
