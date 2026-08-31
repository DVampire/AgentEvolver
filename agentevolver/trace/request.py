"""Versioned, secret-safe snapshots of the requests sent to model providers.

Messages alone are not enough to reproduce an agent decision. The same messages can
produce a different action when the routed model, tool schema, sampling parameters, or
response format changes. This module turns those effective inputs into one immutable
record whose content hash can be cited by trajectories and dataset manifests.

The snapshot deliberately stores an endpoint *fingerprint*, never the endpoint or API
key itself. Reproducibility needs to distinguish two routes; it does not need credentials
or deployment URLs copied into training data.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from enum import Enum
from typing import Any, Dict, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field


REQUEST_SNAPSHOT_VERSION = 2

# Only parameters that can change model behaviour or request handling belong in the
# durable snapshot. Passing arbitrary kwargs through would eventually persist a vendor
# credential, transport object, or callback that happened to ride beside the request.
_BEHAVIOUR_PARAMETERS = (
    "operation",
    "betas",
    "context_management",
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "timeout",
    "reasoning",
    "plugins",
    "stream",
    "max_retries",
    "runtime_features",
    "store",
    "prompt_cache_key",
    "prompt_cache_options",
    "previous_response_id",
    "background",
)


def _jsonable(value: Any) -> Any:
    """Return a stable JSON-safe representation without importing provider serializers.

    Provider wire formats are deliberately not used here: the snapshot describes the
    framework-level request before a provider renames its fields. Pydantic messages and
    tool schemas retain their structure; genuinely opaque values fall back to a string so
    recording remains observational and can never break the model call.
    """
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json", exclude_none=True)
        # Context layer is deliberately excluded from Message.model_dump so no provider
        # serializer or token estimator can mistake protocol metadata for wire content.
        # The snapshot is the one place it belongs: exact layer attribution is evidence.
        layer = getattr(value, "context_layer", None)
        if layer:
            payload["context_layer"] = str(layer)
        return payload
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, set):
        return [_jsonable(item) for item in sorted(value, key=repr)]
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _items(value: Optional[Iterable[Any]]) -> list[Any]:
    """Normalize a request collection without splitting scalar strings into characters."""
    if value is None:
        return []
    if isinstance(value, set):
        return sorted(value, key=repr)
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def endpoint_fingerprint(endpoint: Optional[Any]) -> Optional[str]:
    """Identify an endpoint without placing its URL or embedded credentials in a log."""
    if not endpoint:
        return None
    encoded = str(endpoint).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class RequestSnapshot(BaseModel):
    """The effective, provider-neutral inputs of one model request.

    ``snapshot_id`` is derived from every other field. It is therefore both a compact
    lineage reference and a corruption check: two records with one id cannot describe
    different requests.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=REQUEST_SNAPSHOT_VERSION)
    snapshot_id: str
    requested_model: str
    routed_model: str
    provider: Optional[str] = None
    provider_model: Optional[str] = None
    model_type: Optional[str] = None
    endpoint_fingerprint: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    messages: list[Any] = Field(default_factory=list)
    tools: list[Any] = Field(default_factory=list)
    response_format: Optional[Any] = None
    pressure: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Deterministic accounting for any provider-bound tool-result pruning.",
    )

    @classmethod
    def capture(
        cls,
        *,
        requested_model: str,
        routed_model: str,
        model_config: Any,
        client: Any,
        messages: Iterable[Any],
        tools: Optional[Iterable[Any]],
        response_format: Any,
        request_input: Dict[str, Any],
        call_kwargs: Dict[str, Any],
        stream: bool,
        pressure: Optional[Dict[str, Any]] = None,
    ) -> "RequestSnapshot":
        """Capture final framework-level values immediately before provider dispatch."""
        parameters: Dict[str, Any] = {}
        for name in _BEHAVIOUR_PARAMETERS:
            if name == "stream":
                value = stream
            elif name == "max_retries":
                value = request_input.get(name)
            elif name == "plugins":
                value = request_input.get(name)
                if value is None and model_config is not None:
                    value = getattr(model_config, name, None)
            elif name in call_kwargs:
                value = call_kwargs[name]
            elif name in request_input:
                value = request_input[name]
            else:
                value = getattr(model_config, name, None) if model_config is not None else None
                if value is None and client is not None:
                    value = getattr(client, name, None)
            if value is not None:
                parameters[name] = _jsonable(value)

        body = {
            "schema_version": REQUEST_SNAPSHOT_VERSION,
            "requested_model": requested_model,
            "routed_model": routed_model,
            "provider": (
                getattr(model_config, "provider", None)
                or getattr(client, "provider", None)
            ),
            "provider_model": (
                getattr(model_config, "model_id", None)
                or getattr(client, "model", None)
            ),
            "model_type": getattr(model_config, "model_type", None),
            "endpoint_fingerprint": endpoint_fingerprint(
                getattr(model_config, "api_base", None)
                or getattr(client, "base_url", None)
            ),
            "parameters": parameters,
            "messages": _jsonable(_items(messages)),
            "tools": _jsonable(_items(tools)),
            "response_format": _jsonable(response_format),
            "pressure": _jsonable(pressure),
        }
        return cls(snapshot_id=f"sha256:{_canonical_hash(body)}", **body)


__all__ = [
    "REQUEST_SNAPSHOT_VERSION",
    "RequestSnapshot",
    "endpoint_fingerprint",
]
