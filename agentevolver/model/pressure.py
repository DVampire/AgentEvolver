"""Deterministic request-pressure accounting and tool-result pruning.

This is a last-mile guard at the provider boundary, not conversation compaction.  The
append-only Trace retains the complete tool observation; only an old ``ToolMessage`` in
the effective model request may be replaced with a head/tail excerpt.  User, system and
assistant messages are never rewritten here because doing so would change instructions
or sever tool-call structure rather than merely reducing an already-recorded result.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Iterable, Optional

from agentevolver.message.types import ToolMessage


#: 2 adds ``over_capacity`` — whether the prepared request still exceeds what the model
#: can accept, as opposed to ``unresolved``, which only says pruning did not reach the
#: trigger. A reader of version 1 records cannot infer it: a request may sit above the
#: trigger and still fit.
REQUEST_PRESSURE_VERSION = 2
ESTIMATE_METHOD = "canonical_json_utf8_bytes_div_4"
DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_PRUNE_RATIO = 0.85
DEFAULT_TARGET_RATIO = 0.75
MIN_TOOL_RESULT_CHARS = 512


class ContextOverflowError(RuntimeError):
    """A prepared request still exceeds the model's context window.

    Distinguished from an ordinary provider failure because the two want opposite
    handling. A timeout or a rate limit is worth retrying; this is not — the same request
    is sent each time, and the provider rejects it identically, so a retry policy spends
    its whole budget and its backoff on an outcome that was decided before the first call.

    A *different* model is another matter: fallbacks exist partly because context windows
    differ, so this ends one model's attempts rather than the request.
    """

    def __init__(self, message: str, *, pressure: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.pressure = pressure or {}


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


@dataclass(frozen=True)
class RequestTokenEstimator:
    """Tokenizer-backed counter plus an explicit accuracy statement."""

    count_text: Callable[[str], int]
    method: str
    tokenizer_exact: bool
    provider_wire_exact: bool = False

    def count(self, value: Any) -> int:
        return max(1, int(self.count_text(_canonical_json(value))))


class RequestTokenEstimatorRegistrationError(RuntimeError):
    """A custom estimator registration would be ambiguous or overwrite silently."""


_ESTIMATORS: dict[tuple[str, str], RequestTokenEstimator] = {}
_ESTIMATORS_LOCK = RLock()


def register_request_token_estimator(
    *,
    provider: str,
    model: str = "*",
    estimator: RequestTokenEstimator,
    replace: bool = False,
) -> Callable[[], None]:
    """Register a deployment-specific counter and return an identity-safe disposer.

    Exact ``(provider, model)`` registrations win over the provider wildcard. This hook
    is suitable for a local tokenizer or a gateway-owned cached counter. It deliberately
    does not call provider token-count HTTP APIs: doing so on every dispatch would add a
    second remote request, rate-limit surface, and failure mode to the pressure guard.
    """
    key = (str(provider).strip().lower(), str(model or "*").strip().lower())
    if not key[0]:
        raise RequestTokenEstimatorRegistrationError("provider cannot be empty")
    with _ESTIMATORS_LOCK:
        if key in _ESTIMATORS and not replace:
            raise RequestTokenEstimatorRegistrationError(
                f"token estimator for {key[0]}/{key[1]} is already registered"
            )
        _ESTIMATORS[key] = estimator

    def unregister() -> None:
        # Do not let an old plugin's cleanup delete a newer replacement.
        with _ESTIMATORS_LOCK:
            if _ESTIMATORS.get(key) is estimator:
                _ESTIMATORS.pop(key, None)

    return unregister


def resolve_request_token_estimator(
    *, provider: str = "", model: str = ""
) -> Optional[RequestTokenEstimator]:
    """Use a model-native tokenizer only when tiktoken recognizes the model.

    Encoding canonical framework JSON is tokenizer-exact for that text, but provider wire
    formats add their own role/tool framing. ``provider_wire_exact`` therefore remains
    false; post-call provider usage is the only authoritative wire count.
    """
    provider_key = str(provider).strip().lower()
    model_key = str(model).strip().lower()
    with _ESTIMATORS_LOCK:
        registered = _ESTIMATORS.get((provider_key, model_key))
        if registered is None:
            registered = _ESTIMATORS.get((provider_key, "*"))
    if registered is not None:
        return registered
    if provider_key != "openai" or not model:
        return None
    try:
        import tiktoken
        encoding = tiktoken.encoding_for_model(str(model))
    except (ImportError, KeyError):
        return None
    return RequestTokenEstimator(
        count_text=lambda text: len(encoding.encode(text)),
        method=f"tiktoken:{encoding.name}:canonical_json",
        tokenizer_exact=True,
        provider_wire_exact=False,
    )


def estimate_tokens(value: Any) -> int:
    """Stable conservative approximation used when no provider tokenizer is available."""
    encoded = _canonical_json(value).encode("utf-8")
    return max(1, math.ceil(len(encoded) / 4))


def _count(value: Any, estimator: Optional[RequestTokenEstimator]) -> int:
    return estimator.count(value) if estimator is not None else estimate_tokens(value)


def _tool_content(message: Any) -> Optional[str]:
    if isinstance(message, ToolMessage):
        return message.content
    if isinstance(message, dict) and message.get("role") == "tool":
        content = message.get("content")
        return content if isinstance(content, str) else None
    return None


def _replace_tool_content(message: Any, content: str) -> Any:
    if isinstance(message, ToolMessage):
        return message.model_copy(update={"content": content}, deep=True)
    copied = dict(message)
    copied["content"] = content
    return copied


def _excerpt(content: str, keep_chars: int) -> str:
    keep_chars = max(MIN_TOOL_RESULT_CHARS, int(keep_chars))
    if len(content) <= keep_chars:
        return content
    marker = (
        f"\n\n[AgentEvolver request-pressure excerpt: original_chars={len(content)}; "
        "the complete tool result remains in Trace]\n\n"
    )
    payload = max(2, keep_chars - len(marker))
    head = payload // 2
    tail = payload - head
    return content[:head] + marker + content[-tail:]


@dataclass(frozen=True)
class PreparedRequest:
    messages: list[Any]
    pressure: dict[str, Any]


def prepare_messages(
    messages: Iterable[Any],
    *,
    tools: Optional[Iterable[Any]] = None,
    response_format: Any = None,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    reserved_output_tokens: int = 0,
    prune_ratio: float = DEFAULT_PRUNE_RATIO,
    target_ratio: float = DEFAULT_TARGET_RATIO,
    token_estimator: Optional[RequestTokenEstimator] = None,
) -> PreparedRequest:
    """Prepare one provider request, pruning oldest tool results only when pressured."""
    original = list(messages)
    prepared = list(original)
    tool_list = list(tools or [])
    context_window = max(1, int(context_window))
    reserved = max(0, min(int(reserved_output_tokens), context_window - 1))
    input_capacity = max(1, context_window - reserved)
    trigger = max(1, int(input_capacity * float(prune_ratio)))
    target = max(1, int(input_capacity * min(float(target_ratio), float(prune_ratio))))

    envelope = {"messages": prepared, "tools": tool_list,
                "response_format": response_format}
    before = _count(envelope, token_estimator)
    pruned_indices: list[int] = []
    removed_chars = 0

    if before > trigger:
        # Oldest first: recent observations are normally the ones the pending decision
        # depends on.  Re-estimation after every replacement keeps the algorithm simple,
        # deterministic, and correct for multibyte content.
        for index, message in enumerate(original):
            content = _tool_content(message)
            if content is None or len(content) <= MIN_TOOL_RESULT_CHARS:
                continue
            current = _count({"messages": prepared, "tools": tool_list,
                              "response_format": response_format}, token_estimator)
            if current <= target:
                break
            # Token-to-character conversion is only a proposal for the excerpt size.
            # The loop always re-counts the resulting request with the selected meter.
            excess_chars = max(0, (current - target) * 4)
            keep = max(MIN_TOOL_RESULT_CHARS, len(content) - excess_chars)
            replacement = _excerpt(content, keep)
            if replacement == content:
                continue
            prepared[index] = _replace_tool_content(message, replacement)
            pruned_indices.append(index)
            removed_chars += len(content) - len(replacement)

    after = _count({"messages": prepared, "tools": tool_list,
                    "response_format": response_format}, token_estimator)
    pressure = {
        "schema_version": REQUEST_PRESSURE_VERSION,
        "estimate_method": token_estimator.method if token_estimator else ESTIMATE_METHOD,
        "tokenizer_exact": bool(token_estimator and token_estimator.tokenizer_exact),
        "provider_wire_exact": bool(token_estimator and token_estimator.provider_wire_exact),
        "context_window": context_window,
        "reserved_output_tokens": reserved,
        "input_capacity_tokens": input_capacity,
        "prune_ratio": float(prune_ratio),
        "target_ratio": float(target_ratio),
        "estimated_tokens_before": before,
        "estimated_tokens_after": after,
        "pressure_ratio_before": before / input_capacity,
        "pressure_ratio_after": after / input_capacity,
        "triggered": before > trigger,
        "pruned_message_indices": pruned_indices,
        "removed_chars": removed_chars,
        # Pruning ran and did not get back under the trigger. Observability, not a
        # verdict: a request between the trigger and the capacity is large but valid.
        "unresolved": after > trigger,
        # The verdict. Only tool results may be reduced here — rewriting user, system or
        # assistant messages would change instructions or sever tool-call structure — so a
        # history that is mostly reasoning and instructions can exceed the window with
        # nothing left that this layer is allowed to shrink. Reducing *that* is
        # conversation compaction's job, one level up, and this flag is how the boundary
        # says it could not do the job alone.
        "over_capacity": after > input_capacity,
    }
    return PreparedRequest(messages=prepared, pressure=pressure)


__all__ = [
    "REQUEST_PRESSURE_VERSION",
    "ContextOverflowError",
    "ESTIMATE_METHOD",
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_PRUNE_RATIO",
    "DEFAULT_TARGET_RATIO",
    "PreparedRequest",
    "RequestTokenEstimator",
    "RequestTokenEstimatorRegistrationError",
    "register_request_token_estimator",
    "resolve_request_token_estimator",
    "estimate_tokens",
    "prepare_messages",
]
