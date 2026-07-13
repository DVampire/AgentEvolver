from __future__ import annotations
import json as _json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from src.session import BaseContext


class ModelContext(BaseContext):
    """Context passed into model manager and individual model invocations."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(description="Unique session/call identifier.")
    name: Optional[str] = Field(default=None, description="Human-readable label for this invocation context.")
    work_dir: Optional[str] = Field(default=None, description="Working directory available to the caller.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this context.")


class ModelConfig(BaseModel):
    """Configuration container describing a single LLM/provider pairing."""

    model_name: str = Field(description="Human-readable name used across the codebase.")
    model_type: str = Field(description="Model type, e.g. 'chat/completions', 'responses', 'embeddings'.")
    model_id: str = Field(description="Provider-specific identifier passed to the API.")
    provider: str = Field(description="Provider slug, e.g. 'openai', 'anthropic'.")
    api_base: Optional[str] = Field(default=None, description="Override API base URL.")
    api_key: Optional[str] = Field(default=None, description="Override API key.")
    temperature: Optional[float] = Field(default=None, description="Temperature parameter for the model.")
    reasoning: Optional[Dict[str, Any]] = Field(default={
        "reasoning_effort": "high"
    }, description="Reasoning configuration.")
    plugins: Optional[List[Dict[str, Any]]] = Field(default=None, description="Plugins to use for the model.")
    max_completion_tokens: Optional[int] = Field(default=None, description="Maximum completion tokens for chat/completions models.")
    max_output_tokens: Optional[int] = Field(default=None, description="Maximum output tokens for responses API models.")
    supports_streaming: bool = Field(default=True, description="Whether streaming is supported.")
    supports_functions: bool = Field(default=False, description="Whether tool/function calling is supported.")
    supports_vision: bool = Field(default=False, description="Whether multimodal inputs are supported.")
    output_version: Optional[str] = Field(
        default=None,
        description="Optional output schema version when required by provider.",
    )
    timeout: Optional[float] = Field(default=None, description="Request timeout in seconds.")
    key_pool_name: Optional[str] = Field(default=None, description="Key pool name for round-robin key lookup. Defaults to provider if not set.")
    fallback_model: Optional[str] = Field(
        default=None,
        description="Fallback model name to use if the primary model fails due to policy/content filter errors.",
    )


class TokenUsage(BaseModel):
    """Structured token usage from a single LLM API call."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost: Optional[float] = None

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_write_tokens + self.cache_read_tokens

    @classmethod
    def from_raw(cls, raw: Optional[Dict[str, Any]]) -> Optional["TokenUsage"]:
        """Normalize provider-specific usage dicts into TokenUsage."""
        if not raw:
            return None
        # cache_read: OpenRouter returns in prompt_tokens_details.cached_tokens
        cache_read = (
            raw.get("cache_read_input_tokens") or
            (raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        )
        # cost: OpenRouter returns top-level cost field
        cost_raw = raw.get("cost")
        cost = float(cost_raw) if cost_raw is not None else None
        return cls(
            input_tokens=(
                raw.get("prompt_tokens") or raw.get("input_tokens") or
                raw.get("prompt_token_count") or 0
            ),
            output_tokens=(
                raw.get("completion_tokens") or raw.get("output_tokens") or
                raw.get("candidates_token_count") or 0
            ),
            cache_write_tokens=raw.get("cache_creation_input_tokens") or 0,
            cache_read_tokens=cache_read,
            cost=cost,
        )

    def summary_line(self, model: str = "") -> str:
        parts = [f"in={self.input_tokens}", f"out={self.output_tokens}"]
        if self.cache_write_tokens:
            parts.append(f"cache_write={self.cache_write_tokens}")
        if self.cache_read_tokens:
            parts.append(f"cache_read={self.cache_read_tokens}")
        if self.cost is not None:
            parts.append(f"cost=${self.cost:.6f}")
        prefix = f"[{model}] " if model else ""
        return f"{prefix}tokens: {', '.join(parts)}"


# ---------------------------------------------------------------------------
# Canonical tool-calling + streaming representation (provider-agnostic)
# ---------------------------------------------------------------------------
# The agent and capability layers only ever see these types. Each provider's
# serializer converts to/from its own wire format (tool_use / tool_calls /
# functionCall; input_json_delta / arguments fragments / whole part), so format
# differences never leak past the provider boundary.


class ToolCall(BaseModel):
    """A normalized 'model wants to call tool X' — input is always a parsed dict."""
    id: str = ""
    name: str = ""
    input: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """A normalized tool result to feed back to the model."""
    tool_call_id: str
    content: str
    is_error: bool = False


# --- canonical stream events (dataclasses = cheap on the hot path) ---
@dataclass
class TextDelta:
    text: str


@dataclass
class ThinkingDelta:
    text: str


@dataclass
class ToolCallStart:
    index: int
    id: str
    name: str


@dataclass
class ToolCallArgsDelta:
    index: int
    partial_json: str


@dataclass
class ToolCallComplete:
    """Whole-part providers (Gemini) emit the tool call in one piece."""
    index: int
    id: str
    name: str
    input: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamDone:
    stop_reason: Optional[str] = None          # canonical: tool_use | end_turn | max_tokens | ...
    usage: Optional[Dict[str, Any]] = None     # raw provider usage dict (TokenUsage.from_raw handles it)


StreamEvent = Any  # union of the dataclasses above


def normalize_stop_reason(raw: Optional[str]) -> Optional[str]:
    """Map any provider's finish/stop reason to the canonical vocabulary."""
    if raw is None:
        return None
    r = str(raw).lower()
    if r in ("tool_use", "tool_calls", "function_call"):
        return "tool_use"
    if r in ("end_turn", "stop", "stop_sequence"):
        return "end_turn"
    if r in ("max_tokens", "length", "max_output_tokens"):
        return "max_tokens"
    if r in ("refusal",):
        return "refusal"
    if r in ("pause_turn",):
        return "pause_turn"
    return r


async def accumulate_stream(events: "AsyncIterator[StreamEvent]") -> Dict[str, Any]:
    """Fold a canonical event stream into a buffered result.

    Returns ``{text, thinking, tool_calls: List[ToolCall], stop_reason, usage}``.
    Lets the buffered ``__call__`` path be implemented on top of streaming, and
    lets the agent get a final message after consuming a stream.
    """
    text_parts: List[str] = []
    thinking_parts: List[str] = []
    # index -> {"id","name","args"(str, for fragment providers)|"input"(dict, whole)}
    tools: Dict[int, Dict[str, Any]] = {}
    stop_reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None

    async for ev in events:
        if isinstance(ev, TextDelta):
            text_parts.append(ev.text)
        elif isinstance(ev, ThinkingDelta):
            thinking_parts.append(ev.text)
        elif isinstance(ev, ToolCallStart):
            slot = tools.setdefault(ev.index, {"id": "", "name": "", "args": ""})
            if ev.id:
                slot["id"] = ev.id
            if ev.name:
                slot["name"] = ev.name
        elif isinstance(ev, ToolCallArgsDelta):
            slot = tools.setdefault(ev.index, {"id": "", "name": "", "args": ""})
            slot["args"] = slot.get("args", "") + ev.partial_json
        elif isinstance(ev, ToolCallComplete):
            tools[ev.index] = {"id": ev.id, "name": ev.name, "input": ev.input}
        elif isinstance(ev, StreamDone):
            stop_reason = ev.stop_reason
            usage = ev.usage

    tool_calls: List[ToolCall] = []
    for idx in sorted(tools):
        t = tools[idx]
        if "input" in t:                     # whole-part provider
            parsed = t["input"] or {}
        else:                                # fragment provider: join + json.loads
            raw = (t.get("args") or "").strip()
            try:
                parsed = _json.loads(raw) if raw else {}
            except Exception:
                parsed = {"__raw__": raw}
        tool_calls.append(ToolCall(id=t.get("id") or f"call_{idx}", name=t.get("name", ""), input=parsed))

    return {
        "text": "".join(text_parts),
        "thinking": "".join(thinking_parts),
        "tool_calls": tool_calls,
        "stop_reason": stop_reason,
        "usage": usage,
    }


async def build_response_from_stream(
    events: "AsyncIterator[StreamEvent]",
    *,
    tools: Any = None,
    response_format: Any = None,
) -> Any:
    """Fold a canonical event stream into a buffered ``Response`` — same shape as
    each provider's ``_format_response`` (functions / parsed_model / plain text).

    This is the single place the streaming path builds a buffered result, so
    ``__call__(stream=True)`` on every provider returns exactly what the
    non-streaming path would. Structured output stays pydantic: when
    ``response_format`` is a ``BaseModel`` subclass, the accumulated text is
    parsed and validated into it and returned as ``Response.parsed_model``.
    """
    from src.response.types import Response, ResponseType

    acc = await accumulate_stream(events)
    usage = TokenUsage.from_raw(acc.get("usage"))
    common: Dict[str, Any] = {
        "usage": acc.get("usage"),
        "stop_reason": acc.get("stop_reason"),
        "text": acc.get("text", ""),
        "thinking": acc.get("thinking", ""),
    }

    # 1) Tool calls (native tool calling)
    if tools and acc["tool_calls"]:
        functions = []
        lines = []
        for c in acc["tool_calls"]:
            functions.append({"id": c.id, "name": c.name, "args": c.input})
            if c.input:
                args_str = ", ".join(f"{k}={v!r}" for k, v in c.input.items())
                lines.append(f"Calling function {c.name}({args_str})")
            else:
                lines.append(f"Calling function {c.name}()")
        return Response(
            type=ResponseType.LLM, success=True, message="\n".join(lines),
            data={**common, "functions": functions}, usage=usage,
        )

    # 2) Structured output (pydantic BaseModel → parsed_model)
    if isinstance(response_format, type) and issubclass(response_format, BaseModel):
        text = acc.get("text", "")
        if not text:
            return Response(type=ResponseType.LLM, success=False,
                            message="Empty response content from model", data=common)
        try:
            parsed = response_format.model_validate(_json.loads(text))
        except Exception as e:
            return Response(type=ResponseType.LLM, success=False,
                            message=f"Failed to parse structured output: {e}",
                            data={**common, "content": text})
        model_name = response_format.__name__
        field_lines = [f"{k}={v!r}" for k, v in parsed.model_dump().items()]
        msg = f"Response result:\n\n{model_name}(\n" + ",\n".join(f"    {l}" for l in field_lines) + "\n)"
        return Response(type=ResponseType.LLM, success=True, message=msg,
                        data=common, usage=usage, parsed_model=parsed)

    # 3) Plain text
    return Response(type=ResponseType.LLM, success=True, message=acc.get("text", ""),
                    data=common, usage=usage)


async def buffered_response_to_events(response: Any) -> "AsyncIterator[StreamEvent]":
    """Emit canonical stream events from a final buffered ``Response``.

    Graceful-degradation for providers whose client cannot truly stream
    (custom single-POST REST clients): the whole response is delivered at once
    as canonical events, so ``model_manager.stream()`` presents a uniform
    interface across all providers (SDK-backed stream token-by-token; the rest
    emit the full result in one shot).
    """
    data = getattr(response, "data", None) or {}
    reasoning = data.get("reasoning") or data.get("thinking")
    if reasoning:
        yield ThinkingDelta(str(reasoning))
    functions = data.get("functions")
    if functions:
        for i, fn in enumerate(functions):
            yield ToolCallComplete(
                index=i, id=fn.get("id") or f"call_{i}",
                name=fn.get("name", ""), input=fn.get("args") or {},
            )
    else:
        text = data.get("text")
        if text is None:
            text = getattr(response, "message", "") or ""
        if text:
            yield TextDelta(text)
    yield StreamDone(
        stop_reason=normalize_stop_reason(data.get("stop_reason") or data.get("finish_reason")),
        usage=data.get("usage"),
    )


__all__ = [
    "ModelContext", "ModelConfig", "TokenUsage",
    "ToolCall", "ToolResult",
    "TextDelta", "ThinkingDelta", "ToolCallStart", "ToolCallArgsDelta",
    "ToolCallComplete", "StreamDone", "StreamEvent",
    "normalize_stop_reason", "accumulate_stream", "build_response_from_stream",
    "buffered_response_to_events",
]

