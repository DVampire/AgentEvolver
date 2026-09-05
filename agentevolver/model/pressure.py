"""Read-only request accounting. Only conversation compaction may replace history."""

from __future__ import annotations

import base64
import io
import json
import math
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Iterable, Optional

#: Version 3 never omits text and accounts for visual inputs separately from base64.
REQUEST_PRESSURE_VERSION = 3
ESTIMATE_METHOD = "canonical_json_utf8_bytes_div_4+visual_budget"
#: What a model is assumed to accept when its spec does not say. Every frontier model
#: this repository calls is at or above 1M, and the cost of the two mistakes is not
#: symmetric: too low is a wall we invented — the request never leaves, and history is
#: folded to get under a limit the provider does not have — while too high is a wall the
#: provider states, which `provider_rejected_for_length` turns back into the same
#: recoverable overflow. Guess high, and let the provider correct the guess.
DEFAULT_CONTEXT_WINDOW = 1_000_000
DEFAULT_PRUNE_RATIO = 0.85
DEFAULT_TARGET_RATIO = 0.75


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


def _image_budget(part: dict[str, Any]) -> int:
    """Portable estimate, NOT a provider's billed image count.

    Use unresized 32px patches with a safety allowance. Providers differ in resizing,
    multipliers and detail handling; actual usage remains authoritative. No URL fetches
    or image transformations occur here. A route can register its own image counter.
    """
    image = part.get("image_url", part.get("source", {}))
    url = image.get("url", "") if isinstance(image, dict) else image
    data = image.get("data", "") if isinstance(image, dict) else ""
    if isinstance(url, str) and url.startswith("data:image/") and ";base64," in url:
        data = url.split(",", 1)[1]
    if data:
        try:
            from PIL import Image
        except ImportError:
            return 4096
        try:
            with Image.open(io.BytesIO(base64.b64decode(data, validate=True))) as image_file:
                width, height = image_file.size
            return 256 + 2 * math.ceil(width / 32) * math.ceil(height / 32)
        except (TypeError, ValueError, OSError, Image.DecompressionBombError):
            pass
    # Remote/opaque/malformed images have unknown dimensions. Never charge their URL
    # or binary encoding as language tokens, nor pretend they cost zero.
    return 4096


def _accounting(value: Any, count_image: Callable[[dict[str, Any]], int] = _image_budget):
    """Build an accounting copy; never alter the real messages or text strings."""
    image_tokens, image_count = 0, 0

    def visit(item: Any) -> Any:
        nonlocal image_tokens, image_count
        if isinstance(item, dict):
            kind = item.get("type")
            if isinstance(kind, str) and kind in {"image_url", "input_image", "image"} and any(
                key in item for key in ("image_url", "source", "file_id")
            ):
                image_count += 1
                image_tokens += max(1, int(count_image(item)))
                return {"type": item["type"]}
            return {key: visit(entry) for key, entry in item.items()}
        if isinstance(item, list):
            return [visit(entry) for entry in item]
        return item

    text = _canonical_json(visit(_jsonable(value)))
    return text, image_tokens, image_count


@dataclass(frozen=True)
class RequestTokenEstimator:
    """Tokenizer-backed counter plus an explicit accuracy statement."""

    count_text: Callable[[str], int]
    method: str
    tokenizer_exact: bool
    provider_wire_exact: bool = False
    count_image: Optional[Callable[[dict[str, Any]], int]] = None

    def count(self, value: Any) -> int:
        text, image_tokens, _ = _accounting(value, self.count_image or _image_budget)
        return max(1, int(self.count_text(text)) + image_tokens)


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
    """Approximate text plus visual tokens, independent of image encoding size."""
    text, image_tokens, _ = _accounting(value)
    return max(1, math.ceil(len(text.encode("utf-8")) / 4) + image_tokens)


def _count(value: Any, estimator: Optional[RequestTokenEstimator]) -> int:
    return estimator.count(value) if estimator is not None else estimate_tokens(value)


#: What each provider says when the request exceeded *its* window. Matched as lowercase
#: substrings against the error text, because the wire shape differs per provider (an
#: OpenAI ``code``, an Anthropic ``message``, a relay's passthrough string) while the
#: sentence does not. Deliberately specific: "max_tokens is too large" is about the
#: *output* reservation and must not land here, since folding history would not fix it.
_LENGTH_REJECTION_MARKERS = (
    "context_length_exceeded",           # OpenAI error code
    "maximum context length",            # OpenAI prose
    "prompt is too long",                # Anthropic
    "exceeds the maximum number of tokens",   # Google
    "input token count",                 # Google, paired with the line above
    "context length",                    # OpenRouter passthrough
    "too many total text bytes",         # Google, non-token phrasing
    "reduce the length of the messages",  # OpenAI remediation sentence
)


def provider_rejected_for_length(error: BaseException) -> bool:
    """Whether ``error`` is a provider saying the request exceeded its context window.

    The window we configure is a guess used to detect excessive request pressure.
    The provider's own limit is the only authority, and it states it in a rejection. Read
    here, that rejection becomes the same recoverable condition as a locally-detected
    overflow: the run folds history and rebuilds, rather than retrying an identical
    request until the attempt budget is gone and reporting it as a provider outage.

    Without this, guessing the window high is unsafe — which is the only reason the
    default was ever a number small enough to be wrong in the other direction.
    """
    if isinstance(error, ContextOverflowError):
        return True
    text = f"{error}".lower()
    return any(marker in text for marker in _LENGTH_REJECTION_MARKERS)


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
    """Measure one request without omitting anything, even when over capacity.

    Legacy pruning arguments/metrics remain readable for snapshot compatibility;
    callers recover an overflow by explicitly compacting closed conversation turns.
    """
    prepared = list(messages)
    tool_list = list(tools or [])
    context_window = max(1, int(context_window))
    reserved = max(0, min(int(reserved_output_tokens), context_window - 1))
    input_capacity = max(1, context_window - reserved)
    trigger = max(1, int(input_capacity * float(prune_ratio)))

    envelope = {"messages": prepared, "tools": tool_list,
                "response_format": response_format}
    before = _count(envelope, token_estimator)
    after = before
    _, image_tokens, image_count = _accounting(
        envelope, (token_estimator.count_image if token_estimator else None) or _image_budget,
    )
    context_layers: dict[str, dict[str, int]] = {}
    for layer in ("fixed", "checkpoint", "recent", "live"):
        layer_messages = [
            message for message in prepared
            if getattr(message, "context_layer", None) == layer
        ]
        context_layers[layer] = {
            "messages": len(layer_messages),
            "tokens": _count(layer_messages, token_estimator) if layer_messages else 0,
        }
    pressure = {
        "schema_version": REQUEST_PRESSURE_VERSION,
        "estimate_method": token_estimator.method if token_estimator else ESTIMATE_METHOD,
        "tokenizer_exact": bool(token_estimator and token_estimator.tokenizer_exact),
        "provider_wire_exact": bool(token_estimator and token_estimator.provider_wire_exact
                                    and (not image_count or token_estimator.count_image)),
        "image_count": image_count,
        "image_tokens_estimated": image_tokens,
        "image_estimate_method": "registered" if token_estimator and token_estimator.count_image
                                 else "unresized_32px_patches_x2_plus_256;unknown=4096",
        "text_policy": "preserve",
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
        "pruned_message_indices": [],
        "removed_chars": 0,
        "context_layers": context_layers,
        # Above the warning threshold may still fit. No text is rewritten here.
        "unresolved": after > trigger,
        # Explicit history compaction belongs to the conversation layer.
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
    "provider_rejected_for_length",
    "PreparedRequest",
    "RequestTokenEstimator",
    "RequestTokenEstimatorRegistrationError",
    "register_request_token_estimator",
    "resolve_request_token_estimator",
    "estimate_tokens",
    "prepare_messages",
]
