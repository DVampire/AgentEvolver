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

Unlike the older client, this adapter implements the native Responses event stream. It
normalizes text, reasoning and function-argument deltas into the framework's canonical
events while retaining exact provider output items for stateless replay.
"""

from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional, Union

import httpx
from pydantic import BaseModel, ConfigDict, PrivateAttr

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


class NativeFeatureUnavailable(RuntimeError):
    """An advertised native optimization was rejected; retry via framework fallback."""

    def __init__(self, features: List[str], cause: Exception):
        self.features = list(features)
        self.cause = cause
        super().__init__(
            f"native feature unavailable ({', '.join(self.features)}): {cause}"
        )


def _rejected_features(error: Exception, active: List[str]) -> List[str]:
    """Identify only the native feature the provider explicitly rejected.

    Several optimizations can share one request. A generic HTTP 400 is not evidence that
    all of them are unsupported, so multi-feature requests downgrade only when the error
    names the rejected parameter. A lone optimization may use the status code itself.
    """
    status = getattr(error, "status_code", None)
    text = str(error).lower()
    markers = {
        "programmatic_tool_calling": (
            "programmatic_tool_calling", "allowed_callers", "unknown tool type",
            "programmatic tool",
        ),
        "multi_agent": (
            "multi_agent", "multi-agent", "responses_multi_agent", "beta header",
        ),
        "prompt_cache": (
            "prompt_cache", "prompt cache", "cache key", "cache_options",
        ),
    }
    named = [
        feature for feature in active
        if any(marker in text for marker in markers.get(feature, (feature,)))
    ]
    if named:
        return named
    # Only multi-agent switches from `/responses` to the beta surface. An endpoint-level
    # rejection therefore identifies that feature even when programs/cache share the
    # same request and the relay returns no useful parameter name.
    if status in (404, 405, 501) and "multi_agent" in active:
        return ["multi_agent"]
    rejected_status = status in (400, 404, 405, 409, 422, 501)
    generic_rejection = any(
        marker in text for marker in ("unsupported", "not supported", "unavailable")
    )
    if len(active) == 1 and (rejected_status or generic_rejection):
        return list(active)
    return []


def _dump(value: Any) -> Dict[str, Any]:
    """Dump SDK or relay objects without noisy union-mismatch warnings."""
    if not hasattr(value, "model_dump"):
        return dict(value)
    try:
        return value.model_dump(warnings=False, exclude_none=True)
    except TypeError:  # small test doubles and older Pydantic versions
        return value.model_dump()


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
                # /responses/compact returns all user messages plus one compaction item,
                # but not the current system/developer instructions. Keep only those
                # stable instruction items from the logical fixed layer, then install the
                # provider output verbatim. Keeping the user task as well would duplicate
                # it; dropping system would silently remove the agent contract.
                instructions = [
                    item for item in items if item.get("role") in ("system", "developer")
                ]
                items = [*instructions, *[dict(item) for item in native]]
                continue
        role = getattr(message, "role", "user")

        if role == "tool":
            output = {
                "type": "function_call_output",
                "call_id": getattr(message, "tool_call_id", ""),
                "output": _content_text(getattr(message, "content", "")),
            }
            caller = getattr(message, "caller", None)
            if caller:
                output["caller"] = dict(caller)
            items.append(output)
            continue

        if role == "assistant":
            state = (getattr(message, "provider_state", None) or {}).get("responses") or {}
            output_items = state.get("output_items") or []
            if output_items:
                # Stateless Responses continuation requires every provider output item
                # in its original order. This covers encrypted reasoning, programs,
                # program_output, multi-agent items, messages and function calls.
                items.extend(dict(item) for item in output_items)
                continue
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


def serialize_tools(
    tools: List["Tool"], *, programmatic: bool = False,
) -> List[Dict[str, Any]]:
    """Tool schemas in the Responses shape — flat, not nested under ``function``."""
    from agentevolver.model.llm_hub.serializer import LLMHubChatSerializer

    flattened = []
    serialized_tools = LLMHubChatSerializer.serialize_tools(tools) or []
    for source, serialized in zip(tools, serialized_tools):
        function = serialized.get("function") or {}
        item = {
            "type": "function",
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        }
        if programmatic and bool((getattr(source, "metadata", None) or {}).get("programmatic")):
            item["allowed_callers"] = ["direct", "programmatic"]
        flattened.append(item)
    return flattened


class ResponseLLMHub(BaseModel):
    """Call a Responses-API model and return the canonical buffered `Response`."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    model: str
    provider_name: str = "llm_hub"
    api_key: Optional[str] = None
    base_url: Optional[Union[str, httpx.URL]] = None
    reasoning: Optional[Dict[str, Any]] = None
    max_output_tokens: Optional[int] = 16384
    # AgentEvolver owns durable conversation state in Trace. Stateless Responses keeps
    # provider switching deterministic and returns replayable encrypted reasoning items.
    store: bool = False
    timeout: Optional[Union[float, httpx.Timeout]] = 600.0
    #: Retries live in one place: `ModelContextManager.__call__`, which backs off, records
    #: each failed attempt in the trace, and knows the caller. Handing the SDK a budget of
    #: its own multiplies with that one — three application attempts over five SDK attempts
    #: is fifteen requests nobody chose — and those inner attempts are invisible to both the
    #: log and the trajectory. Raise this only for a provider whose SDK retry does something
    #: ours cannot.
    max_retries: int = 0
    persisted_reasoning: bool = False
    native_programmatic_tool_calling: bool = False
    native_multi_agent: bool = False
    _disabled_features: set[str] = PrivateAttr(default_factory=set)

    @property
    def provider(self) -> str:
        return self.provider_name

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
        if "prompt_cache" in self._disabled_features:
            kwargs.pop("prompt_cache_key", None)
            kwargs.pop("prompt_cache_options", None)
        params: Dict[str, Any] = {
            "model": self.model,
            "input": serialize_input(messages),
            "store": self.store,
        }
        if self.max_output_tokens:
            params["max_output_tokens"] = self.max_output_tokens
        if self.reasoning:
            # Accepts either the Responses shape (`{"effort": ...}`) or the LLM Hub
            # chat shape (`{"reasoning": {...}}`) the catalog uses elsewhere, so one
            # catalog entry can move between the two surfaces without being rewritten.
            reasoning = self.reasoning.get("reasoning", self.reasoning)
            if isinstance(reasoning, dict):
                supported = {
                    key: reasoning[key]
                    for key in ("effort", "context", "mode", "summary")
                    if key in reasoning
                }
                if supported:
                    params["reasoning"] = supported
        features = kwargs.pop("runtime_features", None) or {}
        use_programmatic = bool(
            features.get("programmatic_tool_calling") == "native"
            and self.native_programmatic_tool_calling
            and "programmatic_tool_calling" not in self._disabled_features
        )
        use_multi_agent = bool(
            features.get("multi_agent") == "native"
            and self.native_multi_agent
            and "multi_agent" not in self._disabled_features
        )
        if tools:
            serialized = serialize_tools(tools, programmatic=use_programmatic)
            if serialized:
                if use_programmatic and any("programmatic" in item.get("allowed_callers", []) for item in serialized):
                    serialized.append({"type": "programmatic_tool_calling"})
                params["tools"] = serialized
        params.update({k: v for k, v in kwargs.items() if v is not None})
        if use_multi_agent:
            # These parameters are rejected by the beta surface. Resolve the conflict
            # here so provider-specific constraints do not leak into Agent/MetaAgent.
            if isinstance(params.get("reasoning"), dict):
                params["reasoning"].pop("summary", None)
            params.pop("max_tool_calls", None)
            params["multi_agent"] = {
                "enabled": True,
                "max_concurrent_subagents": int(features.get("max_concurrent_subagents") or 3),
            }
            params["betas"] = ["responses_multi_agent=v1"]
        return params

    def _parse(self, raw: Any) -> Response:
        """Fold the Responses output list into the buffered `Response` shape.

        `data` uses the keys `buffered_response_to_events` reads — `functions` entries
        keyed `id` / `name` / `args` — because that adapter is how this client's result
        becomes a stream for the agent loop.
        """
        payload = _dump(raw)
        text_parts: List[str] = []
        reasoning_parts: List[str] = []
        reasoning_items: List[Dict[str, Any]] = []
        functions: List[Dict[str, Any]] = []
        output_items = [dict(item) for item in payload.get("output") or []]

        message_items = [item for item in output_items if item.get("type") == "message"]
        root_final = [
            item for item in message_items
            if ((item.get("agent") or {}).get("agent_name") == "/root"
                and item.get("phase") == "final_answer")
        ]
        visible_messages = root_final or [
            item for item in message_items if not item.get("agent")
        ] or message_items

        for item in output_items:
            item_type = item.get("type")
            if item_type == "message":
                if item not in visible_messages:
                    continue
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
                    "caller": item.get("caller"),
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
            success=payload.get("status") in (
                None, "queued", "in_progress", "completed", "cancelling", "cancelled",
            ),
            message=message,
            data={
                "text": text,
                "functions": functions,
                "reasoning": "".join(reasoning_parts),
                "provider_state": {
                    "responses": {
                        "output_items": output_items,
                        # Backward-compatible diagnostic; output_items is authoritative.
                        "reasoning_items": reasoning_items,
                    }
                } if output_items else {},
                "usage": usage,
                "finish_reason": "tool_use" if functions else "end_turn",
                "raw_response": payload,
                "background": {
                    "response_id": payload.get("id"),
                    "status": payload.get("status"),
                } if payload.get("id") else None,
            },
            usage=TokenUsage.from_raw(usage),
        )

    async def compact_history(self, messages: List[Message]) -> Dict[str, Any]:
        """Compact canonical history with the native Responses endpoint.

        Its output is a canonical replacement input. It must be replayed as-is; the
        provider serializer retains stable system/developer instructions, replaces the
        older conversation with this output, and then appends only newer turns.
        """
        client = self._client()
        raw = await client.responses.compact(
            model=self.model,
            input=serialize_input(messages),
        )
        payload = _dump(raw)
        output = [dict(item) for item in payload.get("output") or []]
        if not any(item.get("type") == "compaction" for item in output):
            raise RuntimeError("Responses compaction returned no compaction item")

        return {
            "provider_state": {"responses": {"compaction_items": output}},
            "usage": payload.get("usage") or {},
            "format": "openai.responses.compaction",
            "native": True,
        }

    async def _create(self, client: Any, params: Dict[str, Any]) -> Any:
        """Create one Responses segment and attribute native feature rejection."""
        try:
            surface = client.beta.responses if params.get("multi_agent") else client.responses
            return await surface.create(**params)
        except Exception as error:
            active = []
            if any(
                tool.get("type") == "programmatic_tool_calling"
                for tool in params.get("tools") or []
            ):
                active.append("programmatic_tool_calling")
            if params.get("multi_agent"):
                active.append("multi_agent")
            if params.get("prompt_cache_key"):
                active.append("prompt_cache")
            rejected = _rejected_features(error, active)
            if rejected:
                self._disabled_features.update(rejected)
                logger.warning(
                    f"| ⚠️ {self.model}: native {', '.join(rejected)} unavailable; "
                    "retrying with provider-neutral fallback"
                )
                raise NativeFeatureUnavailable(rejected, error) from error
            logger.error(
                f"| 🔴 {self.provider_name} responses error (model={self.model}): "
                f"{type(error).__name__}: {error}"
            )
            raise

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
            response_format: Optional native JSON-schema response contract; Pydantic
                contracts are validated again after the final continuation segment.

        Returns:
            The buffered `Response`; `data.functions` carries any tool calls.
        """
        params = self._build_params(messages, tools=tools, **kwargs)
        if response_format is not None:
            from agentevolver.model.llm_hub.serializer import LLMHubChatSerializer
            if (
                isinstance(response_format, type)
                and issubclass(response_format, BaseModel)
            ) or isinstance(response_format, BaseModel):
                model_class = (
                    response_format if isinstance(response_format, type)
                    else type(response_format)
                )
                serialized = LLMHubChatSerializer.serialize_response_format(
                    response_format, model_name=self.model,
                )
                schema = serialized.get("json_schema", {})
                params["text"] = {"format": {
                    "type": "json_schema",
                    "name": model_class.__name__,
                    "strict": schema.get("strict", True),
                    "schema": schema.get("schema", {}),
                }}
            elif isinstance(response_format, dict):
                value = response_format.get("text", response_format)
                params["text"] = value if "format" in value else {"format": value}
            else:
                raise RuntimeError(
                    f"unsupported Responses response_format: {response_format!r}"
                )
        client = self._client()
        raw = await self._create(client, params)
        response = self._parse(raw)
        usages = [response.usage] if response.usage is not None else []
        all_output_items = list(
            (((response.data or {}).get("provider_state") or {}).get("responses") or {})
            .get("output_items") or []
        )
        # A hosted program may finish in one response and emit the root message in the
        # next. Continue internally when there is no client-owned call to dispatch.
        continuation_input = serialize_input(messages)
        for _ in range(3):
            state = ((response.data or {}).get("provider_state") or {}).get("responses") or {}
            output = state.get("output_items") or []
            if response.message or (response.data or {}).get("functions"):
                break
            if not any(item.get("type") in ("program", "program_output", "multi_agent_call") for item in output):
                break
            continuation_input.extend(dict(item) for item in output)
            next_params = dict(params)
            next_params["input"] = list(continuation_input)
            raw = await self._create(client, next_params)
            response = self._parse(raw)
            segment_state = (
                ((response.data or {}).get("provider_state") or {}).get("responses") or {}
            )
            all_output_items.extend(segment_state.get("output_items") or [])
            if response.usage is not None:
                usages.append(response.usage)
        if all_output_items:
            response.data["provider_state"] = {
                "responses": {
                    "output_items": all_output_items,
                    "reasoning_items": [
                        item for item in all_output_items
                        if item.get("type") == "reasoning"
                    ],
                }
            }
        if len(usages) > 1:
            costs = [usage.cost for usage in usages if usage.cost is not None]
            combined = TokenUsage(
                input_tokens=sum(usage.input_tokens for usage in usages),
                context_input_tokens=sum(
                    usage.context_input_tokens for usage in usages
                ),
                output_tokens=sum(usage.output_tokens for usage in usages),
                reasoning_tokens=sum(usage.reasoning_tokens for usage in usages),
                cache_write_tokens=sum(usage.cache_write_tokens for usage in usages),
                cache_read_tokens=sum(usage.cache_read_tokens for usage in usages),
                provider_reported_total=(
                    sum(usage.provider_reported_total or usage.total for usage in usages)
                ),
                cost=sum(costs) if costs else None,
                cost_status=(
                    "reported" if costs and all(
                        usage.cost_status == "reported" for usage in usages
                    ) else "unknown"
                ),
            )
            response.usage = combined
            response.data["usage"] = combined.model_dump()
        if response_format is not None and response.message:
            try:
                if (
                    isinstance(response_format, type)
                    and issubclass(response_format, BaseModel)
                ):
                    response.parsed_model = response_format.model_validate_json(
                        response.message
                    )
                elif isinstance(response_format, BaseModel):
                    response.parsed_model = type(response_format).model_validate_json(
                        response.message
                    )
            except Exception as error:
                return Response(
                    type=ResponseType.LLM,
                    success=False,
                    message=f"Structured response validation failed: {error}",
                    data=response.data,
                    usage=response.usage,
                )
        return response

    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List["Tool"]] = None,
        response_format: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Yield canonical events from the native Responses event stream.

        Exact output items are retained as provider state, while text/reasoning/tool
        arguments arrive incrementally. Hosted program/multi-agent segments may require
        another provider turn; those are continued internally just like ``__call__``.
        """
        from agentevolver.model.types import (
            ProviderState,
            StreamDone,
            TextDelta,
            ThinkingDelta,
            ToolCallArgsDelta,
            ToolCallComplete,
            ToolCallStart,
        )

        params = self._build_params(messages, tools=tools, **kwargs)
        if response_format is not None:
            # Keep structured-output serialization in one implementation. Building the
            # buffered parameters performs no I/O; capture only the resulting text field.
            from agentevolver.model.llm_hub.serializer import LLMHubChatSerializer
            if isinstance(response_format, type) and issubclass(response_format, BaseModel):
                serialized = LLMHubChatSerializer.serialize_response_format(
                    response_format, model_name=self.model,
                )
                schema = serialized.get("json_schema", {})
                params["text"] = {"format": {
                    "type": "json_schema", "name": response_format.__name__,
                    "strict": schema.get("strict", True),
                    "schema": schema.get("schema", {}),
                }}
            elif isinstance(response_format, BaseModel):
                model_class = type(response_format)
                serialized = LLMHubChatSerializer.serialize_response_format(
                    response_format, model_name=self.model,
                )
                schema = serialized.get("json_schema", {})
                params["text"] = {"format": {
                    "type": "json_schema", "name": model_class.__name__,
                    "strict": schema.get("strict", True),
                    "schema": schema.get("schema", {}),
                }}
            elif isinstance(response_format, dict):
                value = response_format.get("text", response_format)
                params["text"] = value if "format" in value else {"format": value}
            else:
                raise RuntimeError(
                    f"unsupported Responses response_format: {response_format!r}"
                )
        params["stream"] = True
        client = self._client()
        continuation_input = list(params.get("input") or [])
        all_output: List[Dict[str, Any]] = []
        combined_usage = TokenUsage()
        final_stop = "end_turn"

        for _segment in range(4):
            segment_items: Dict[int, Dict[str, Any]] = {}
            call_started: set[int] = set()
            call_arguments_seen: set[int] = set()
            saw_visible = False
            saw_client_call = False
            hosted_only = False
            completed_payload: Dict[str, Any] = {}
            try:
                raw_stream = await self._create(client, dict(params))
                async for raw_event in raw_stream:
                    event = _dump(raw_event)
                    event_type = str(event.get("type") or "")
                    index = int(event.get("output_index") or 0)
                    if event_type == "response.output_item.added":
                        item = dict(event.get("item") or {})
                        segment_items[index] = item
                        if item.get("type") == "function_call":
                            saw_client_call = True
                            call_started.add(index)
                            yield ToolCallStart(
                                index=index,
                                id=item.get("call_id") or item.get("id") or "",
                                name=item.get("name") or "",
                                caller=item.get("caller"),
                            )
                        if item.get("type") in (
                            "program", "program_output", "multi_agent_call",
                        ):
                            hosted_only = True
                    elif event_type == "response.function_call_arguments.delta":
                        call_arguments_seen.add(index)
                        yield ToolCallArgsDelta(
                            index=index, partial_json=str(event.get("delta") or ""),
                        )
                    elif event_type == "response.output_text.delta":
                        item = segment_items.get(index) or {}
                        agent = item.get("agent") or {}
                        visible = (
                            not agent
                            or (agent.get("agent_name") == "/root"
                                and item.get("phase") == "final_answer")
                        )
                        if visible:
                            saw_visible = True
                            yield TextDelta(str(event.get("delta") or ""))
                    elif event_type in (
                        "response.reasoning_summary_text.delta",
                        "response.reasoning_text.delta",
                    ):
                        yield ThinkingDelta(str(event.get("delta") or ""))
                    elif event_type == "response.output_item.done":
                        item = dict(event.get("item") or {})
                        segment_items[index] = item
                        if item.get("type") == "function_call" and index in call_started:
                            if index not in call_arguments_seen and item.get("arguments"):
                                yield ToolCallArgsDelta(
                                    index=index,
                                    partial_json=str(item.get("arguments") or ""),
                                )
                        elif item.get("type") == "function_call":
                            import json as _json
                            arguments = item.get("arguments") or "{}"
                            try:
                                parsed = _json.loads(arguments) if isinstance(arguments, str) else arguments
                            except (ValueError, TypeError):
                                parsed = {"__raw__": arguments}
                            saw_client_call = True
                            yield ToolCallComplete(
                                index=index,
                                id=item.get("call_id") or item.get("id") or "",
                                name=item.get("name") or "",
                                input=parsed or {},
                                caller=item.get("caller"),
                            )
                    elif event_type == "response.completed":
                        completed_payload = _dump(event.get("response") or {})
                    elif event_type in ("response.failed", "response.incomplete"):
                        detail = event.get("error") or event.get("response") or event
                        raise RuntimeError(f"Responses stream {event_type}: {detail}")
            except Exception as error:
                active = []
                if any(t.get("type") == "programmatic_tool_calling" for t in params.get("tools") or []):
                    active.append("programmatic_tool_calling")
                if params.get("multi_agent"):
                    active.append("multi_agent")
                if params.get("prompt_cache_key"):
                    active.append("prompt_cache")
                rejected = _rejected_features(error, active)
                if rejected and not isinstance(error, NativeFeatureUnavailable):
                    self._disabled_features.update(rejected)
                    raise NativeFeatureUnavailable(rejected, error) from error
                raise

            exact_items = [dict(item) for item in completed_payload.get("output") or []]
            if not exact_items:
                exact_items = [segment_items[key] for key in sorted(segment_items)]
            all_output.extend(exact_items)
            usage = TokenUsage.from_raw(completed_payload.get("usage") or {})
            if usage is not None:
                combined_usage.input_tokens += usage.input_tokens
                combined_usage.context_input_tokens += usage.context_input_tokens
                combined_usage.output_tokens += usage.output_tokens
                combined_usage.reasoning_tokens += usage.reasoning_tokens
                combined_usage.cache_write_tokens += usage.cache_write_tokens
                combined_usage.cache_read_tokens += usage.cache_read_tokens
                combined_usage.provider_reported_total = (
                    (combined_usage.provider_reported_total or 0)
                    + (usage.provider_reported_total or usage.total)
                )
                if usage.cost is not None:
                    combined_usage.cost = (combined_usage.cost or 0.0) + usage.cost
                    combined_usage.cost_status = usage.cost_status
            final_stop = "tool_use" if saw_client_call else "end_turn"
            if saw_visible or saw_client_call or not hosted_only:
                break
            continuation_input.extend(exact_items)
            params["input"] = list(continuation_input)

        if all_output:
            yield ProviderState({"responses": {
                "output_items": all_output,
                "reasoning_items": [
                    item for item in all_output if item.get("type") == "reasoning"
                ],
            }})
        usage_dict = combined_usage.model_dump()
        yield StreamDone(
            stop_reason=final_stop,
            usage=usage_dict if any(
                usage_dict.get(key) for key in (
                    "input_tokens", "context_input_tokens", "output_tokens",
                    "reasoning_tokens", "cache_write_tokens", "cache_read_tokens",
                    "provider_reported_total", "cost",
                )
            ) else None,
        )

    async def create_background(
        self,
        messages: List[Message],
        tools: Optional[List["Tool"]] = None,
        response_format: Any = None,
        **kwargs: Any,
    ) -> Response:
        """Start a durable provider job without making it conversation authority.

        The returned response id is merely an effect handle. Callers must persist the
        surrounding request/result in Trace and use explicit retrieve/cancel operations;
        no hidden ``previous_response_id`` chain is created.
        """
        kwargs["background"] = True
        kwargs["store"] = True
        return await self(
            messages=messages,
            tools=tools,
            response_format=response_format,
            **kwargs,
        )

    async def retrieve_background(self, response_id: str) -> Response:
        if not response_id:
            raise ValueError("response_id is required")
        raw = await self._client().responses.retrieve(response_id)
        return self._parse(raw)

    async def cancel_background(self, response_id: str) -> Response:
        if not response_id:
            raise ValueError("response_id is required")
        raw = await self._client().responses.cancel(response_id)
        return self._parse(raw)


__all__ = [
    "NativeFeatureUnavailable", "ResponseLLMHub", "serialize_input", "serialize_tools",
]
