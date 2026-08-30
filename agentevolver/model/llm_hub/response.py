"""Responses-API client for models that cannot do tools on chat/completions.

Some reasoning models refuse function tools on `/v1/chat/completions` outright::

    Function tools with reasoning_effort are not supported for gpt-5.6-sol in
    /v1/chat/completions. To use function tools, use /v1/responses or set
    reasoning_effort to 'none'.

Since every AgentEvolver agent loop *is* tool calling, "set reasoning to none" means
giving up the reason for choosing that model. So those models are routed here instead.

Why not reuse `openai/response.py`: that client drops tools on the floor
(``# params["tools"] = tools  # Uncomment if supported``) and has no ``stream()``. It
serves non-tool single-turn use, which is the opposite of what an agent needs.

There is no ``stream()`` here either, deliberately. ``ModelContextManager.stream``
already buffers a client that lacks one and replays the result as canonical events, so
a real token stream would add a second wire format to maintain for no behavioural gain
— an agent step consumes the whole turn before acting on it either way.
"""

from typing import Any, Dict, List, Optional, TYPE_CHECKING, Union

import httpx
from pydantic import BaseModel, ConfigDict

try:
    from openai import AsyncOpenAI
except ImportError:  # the provider is optional at import time
    AsyncOpenAI = None

from agentevolver.logger import logger
from agentevolver.message.types import CompactionMessage, Message
from agentevolver.model.types import TokenUsage
from agentevolver.response.types import Response, ResponseType

if TYPE_CHECKING:
    from agentevolver.tool.types import Tool


def _content_text(content: Any) -> str:
    """The text of one message's content, whether it is a string or content parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            text = getattr(part, "text", None)
            if text is None and isinstance(part, dict):
                text = part.get("text")
            if text:
                parts.append(str(text))
        return "\n".join(parts)
    return "" if content is None else str(content)


def serialize_input(messages: List[Message]) -> List[Dict[str, Any]]:
    """Convert the canonical message list into Responses-API input items.

    The two APIs disagree about what a turn is. Chat has one message per turn, with
    tool calls attached to the assistant's; Responses has a flat item list where a call
    and its result are items in their own right. `tool_call_id` is the hinge, and it is
    the same value on both sides.
    """
    items: List[Dict[str, Any]] = []
    for message in messages:
        if isinstance(message, CompactionMessage):
            state = (message.provider_state or {}).get("responses") or {}
            native = state.get("compaction_items") or []
            if native:
                # Opaque means opaque: replay the exact item returned by /compact.
                items.extend(dict(item) for item in native)
                continue
        role = getattr(message, "role", "user")

        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": getattr(message, "tool_call_id", ""),
                "output": _content_text(getattr(message, "content", "")),
            })
            continue

        if role == "assistant":
            state = (getattr(message, "provider_state", None) or {}).get("responses") or {}
            # Manual Responses history must replay model output items, not flatten
            # reasoning into assistant prose. Keep the exact item returned by the API.
            items.extend(dict(item) for item in state.get("reasoning_items") or [])
            text = _content_text(getattr(message, "content", ""))
            if text:
                items.append({"role": "assistant", "content": text})
            for call in getattr(message, "tool_calls", None) or []:
                function = getattr(call, "function", None)
                items.append({
                    "type": "function_call",
                    "call_id": getattr(call, "id", ""),
                    "name": getattr(function, "name", "") if function else "",
                    "arguments": getattr(function, "arguments", "{}") if function else "{}",
                })
            continue

        # `system` is sent as a role item rather than as `instructions`: the caller may
        # send several, and `instructions` is a single field that would silently keep
        # only the last one.
        items.append({"role": role, "content": _content_text(getattr(message, "content", ""))})
    return items


def serialize_tools(tools: List["Tool"]) -> List[Dict[str, Any]]:
    """Tool schemas in the Responses shape — flat, not nested under ``function``."""
    from agentevolver.model.llm_hub.serializer import LLMHubChatSerializer

    flattened = []
    for tool in LLMHubChatSerializer.serialize_tools(tools) or []:
        function = tool.get("function") or {}
        flattened.append({
            "type": "function",
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        })
    return flattened


class ResponseLLMHub(BaseModel):
    """Call a Responses-API model and return the canonical buffered `Response`."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    model: str
    api_key: Optional[str] = None
    base_url: Optional[Union[str, httpx.URL]] = None
    reasoning: Optional[Dict[str, Any]] = None
    max_output_tokens: Optional[int] = 16384
    timeout: Optional[Union[float, httpx.Timeout]] = 600.0
    #: Retries live in one place: `ModelContextManager.__call__`, which backs off, records
    #: each failed attempt in the trace, and knows the caller. Handing the SDK a budget of
    #: its own multiplies with that one — three application attempts over five SDK attempts
    #: is fifteen requests nobody chose — and those inner attempts are invisible to both the
    #: log and the trajectory. Raise this only for a provider whose SDK retry does something
    #: ours cannot.
    max_retries: int = 0

    @property
    def provider(self) -> str:
        return "llm_hub"

    @property
    def name(self) -> str:
        return self.model

    def set_api_key(self, api_key: str) -> None:
        self.api_key = api_key

    def _client(self) -> Any:
        if AsyncOpenAI is None:
            raise RuntimeError("The `openai` package is required for the Responses API client")
        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=str(self.base_url) if self.base_url else None,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )

    def _build_params(
        self,
        messages: List[Message],
        tools: Optional[List["Tool"]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "model": self.model,
            "input": serialize_input(messages),
        }
        if self.max_output_tokens:
            params["max_output_tokens"] = self.max_output_tokens
        if self.reasoning:
            # Accepts either the Responses shape (`{"effort": ...}`) or the LLM Hub
            # chat shape (`{"reasoning": {...}}`) the catalog uses elsewhere, so one
            # catalog entry can move between the two surfaces without being rewritten.
            reasoning = self.reasoning.get("reasoning", self.reasoning)
            if isinstance(reasoning, dict) and "effort" in reasoning:
                params["reasoning"] = {"effort": reasoning["effort"]}
        if tools:
            serialized = serialize_tools(tools)
            if serialized:
                params["tools"] = serialized
        params.update({k: v for k, v in kwargs.items() if v is not None})
        return params

    def _parse(self, raw: Any) -> Response:
        """Fold the Responses output list into the buffered `Response` shape.

        `data` uses the keys `buffered_response_to_events` reads — `functions` entries
        keyed `id` / `name` / `args` — because that adapter is how this client's result
        becomes a stream for the agent loop.
        """
        payload = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        text_parts: List[str] = []
        reasoning_parts: List[str] = []
        reasoning_items: List[Dict[str, Any]] = []
        functions: List[Dict[str, Any]] = []

        for item in payload.get("output") or []:
            item_type = item.get("type")
            if item_type == "message":
                for part in item.get("content") or []:
                    if part.get("text"):
                        text_parts.append(part["text"])
            elif item_type == "function_call":
                import json as _json

                arguments = item.get("arguments") or "{}"
                try:
                    parsed = _json.loads(arguments) if isinstance(arguments, str) else arguments
                except (ValueError, TypeError):
                    parsed = {"__raw__": arguments}
                functions.append({
                    # `call_id` is the value a later `function_call_output` must echo;
                    # `id` names the output item itself and is not accepted back.
                    "id": item.get("call_id") or item.get("id") or "",
                    "name": item.get("name", ""),
                    "args": parsed,
                })
            elif item_type == "reasoning":
                reasoning_items.append(dict(item))
                for part in item.get("summary") or []:
                    if part.get("text"):
                        reasoning_parts.append(part["text"])

        usage = payload.get("usage") or {}
        text = "".join(text_parts)
        message = text
        if functions and not text:
            message = "\n".join(
                f"Calling function {f['name']}(" +
                ", ".join(f"{k}={v!r}" for k, v in (f["args"] or {}).items()) + ")"
                for f in functions
            )

        return Response(
            type=ResponseType.LLM,
            success=payload.get("status") in (None, "completed"),
            message=message,
            data={
                "text": text,
                "functions": functions,
                "reasoning": "".join(reasoning_parts),
                "provider_state": {
                    "responses": {"reasoning_items": reasoning_items}
                } if reasoning_items else {},
                "usage": usage,
                "finish_reason": "tool_use" if functions else "end_turn",
                "raw_response": payload,
            },
            usage=TokenUsage.from_raw(usage),
        )

    async def compact_history(self, messages: List[Message]) -> Dict[str, Any]:
        """Compact canonical history with the native Responses endpoint.

        The endpoint also returns user messages. AgentEvolver keeps its task anchor on
        the Trace surface, so that first copy is removed; later user items and the opaque
        compaction item are replayed exactly at the replacement point.
        """
        client = self._client()
        raw = await client.responses.compact(
            model=self.model,
            input=serialize_input(messages),
        )
        payload = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        output = [dict(item) for item in payload.get("output") or []]
        if not any(item.get("type") == "compaction" for item in output):
            raise RuntimeError("Responses compaction returned no compaction item")

        # /compact deliberately returns user messages outside the opaque item. The
        # first one is our task anchor, which ContextBuilder already retains as a stable
        # cache prefix. Keep every later user item so compaction cannot erase an injected
        # execution error or correction.
        skipped_anchor = False
        items = []
        for item in output:
            if (
                not skipped_anchor
                and item.get("type") == "message"
                and item.get("role") == "user"
            ):
                skipped_anchor = True
                continue
            items.append(item)
        return {
            "provider_state": {"responses": {"compaction_items": items}},
            "usage": payload.get("usage") or {},
            "format": "openai.responses.compaction",
            "native": True,
        }

    async def __call__(
        self,
        messages: List[Message],
        tools: Optional[List["Tool"]] = None,
        response_format: Any = None,
        **kwargs: Any,
    ) -> Response:
        """Run one turn.

        Args:
            messages: The conversation so far.
            tools: Tools the model may call.
            response_format: Accepted and ignored — structured output is a separate
                Responses feature, and quietly reinterpreting it as a text instruction
                would produce output that looks schema-conformant without being checked.

        Returns:
            The buffered `Response`; `data.functions` carries any tool calls.
        """
        if response_format is not None:
            logger.warning(
                f"| ⚠️ {self.model}: response_format is not supported on the Responses "
                f"client and was ignored"
            )
        params = self._build_params(messages, tools=tools, **kwargs)
        client = self._client()
        try:
            raw = await client.responses.create(**params)
        except Exception as error:
            logger.error(f"| 🔴 llm_hub responses error (model={self.model}): "
                         f"{type(error).__name__}: {error}")
            raise
        return self._parse(raw)


__all__ = ["ResponseLLMHub", "serialize_input", "serialize_tools"]
