"""Runtime evidence for optional provider capabilities.

Catalog flags are eligibility declarations, not proof that a particular relay/model/API
version currently accepts a beta feature.  This registry keeps the two concepts separate:
the caller may try an eligible feature while observations decide whether that exact route
should be used, temporarily downgraded, or probed again after its TTL.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from agentevolver.trace.request import endpoint_fingerprint
from agentevolver.utils.file_utils import atomic_json_update


def project_messages(messages: list, config: Any) -> list:
    """Replay portable history when switching away from provider-owned state.

    Keep source messages untouched: a temporary fallback must not erase the primary's
    state. Legacy untagged state remains unchanged on the same protocol surface.
    """
    from agentevolver.message.types import AssistantMessage, CompactionMessage, ToolMessage

    result = []
    portable_calls = set()
    for message in messages:
        states = getattr(message, "provider_state", None) or {}
        incompatible = any(
            state and (config.model_type != surface
                       or (state.get("model") and state["model"] != config.model_id))
            for namespace, surface in (("responses", "responses"), ("anthropic", "anthropic/messages"))
            if (state := states.get(namespace))
        )
        if incompatible:
            if isinstance(message, CompactionMessage) and not message.text.strip():
                raise ValueError("Cannot switch models: native checkpoint has no portable summary")
            if isinstance(message, AssistantMessage):
                portable_calls.update(call.id for call in message.tool_calls)
                calls = [call.model_copy(update={"caller": None}) for call in message.tool_calls]
                message = message.model_copy(update={"provider_state": {}, "tool_calls": calls})
            else:
                message = message.model_copy(update={"provider_state": {}})
        if isinstance(message, ToolMessage) and message.tool_call_id in portable_calls:
            message = message.model_copy(update={"caller": None})
        result.append(message)
    return result


class CapabilityState(str, Enum):
    UNKNOWN = "unknown"
    PROBING = "probing"
    VERIFIED = "verified"
    REJECTED = "rejected"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class CapabilityRoute:
    provider: str
    endpoint_fingerprint: str
    model: str
    api_version: str

    @classmethod
    def capture(cls, config: Any, client: Any = None) -> "CapabilityRoute":
        endpoint = (
            getattr(config, "api_base", None)
            or getattr(client, "base_url", None)
            or f"default:{getattr(config, 'provider', None) or getattr(client, 'provider', 'unknown')}"
        )
        return cls(
            provider=str(
                getattr(config, "provider", None)
                or getattr(client, "provider", None)
                or "unknown"
            ),
            endpoint_fingerprint=endpoint_fingerprint(endpoint) or "default",
            model=str(
                getattr(config, "model_id", None)
                or getattr(client, "model", None)
                or "unknown"
            ),
            api_version=str(
                getattr(config, "output_version", None)
                or getattr(config, "model_type", None)
                or "unknown"
            ),
        )


@dataclass
class CapabilityObservation:
    state: CapabilityState
    observed_at: float
    expires_at: float
    last_error: Optional[str] = None
    attempts: int = 0

    def as_dict(self, now: float) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "ttl_remaining": max(0.0, self.expires_at - now),
            "last_error": self.last_error,
            "attempts": self.attempts,
        }


class ProviderCapabilityRegistry:
    """TTL-scoped capability observations keyed by an exact provider route."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 3600.0,
        degraded_ttl_seconds: float = 60.0,
        clock: Callable[[], float] = time.time,
        persist_path: Optional[str] = None,
    ) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.degraded_ttl_seconds = max(1.0, float(degraded_ttl_seconds))
        self._clock = clock
        self._entries: Dict[tuple[CapabilityRoute, str], CapabilityObservation] = {}
        self._expired: set[tuple[CapabilityRoute, str]] = set()
        self._persist_path: Optional[Path] = None
        if persist_path:
            self.set_persist_path(persist_path)

    def set_persist_path(self, path: Optional[str]) -> None:
        """Bind a best-effort durable cache and load still-live observations."""
        self._persist_path = Path(path).expanduser() if path else None
        if self._persist_path is not None:
            self._load()

    def _load(self) -> None:
        path = self._persist_path
        if path is None or not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != 1:
                return
            now = self._clock()
            for raw in payload.get("observations") or []:
                route = CapabilityRoute(**dict(raw["route"]))
                observation = CapabilityObservation(
                    state=CapabilityState(str(raw["state"])),
                    observed_at=float(raw["observed_at"]),
                    expires_at=float(raw["expires_at"]),
                    last_error=(
                        str(raw["last_error"])
                        if raw.get("last_error") is not None else None
                    ),
                    attempts=max(0, int(raw.get("attempts") or 0)),
                )
                if observation.expires_at > now:
                    self._entries[(route, str(raw["feature"]))] = observation
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            # Capability evidence is an optimization cache, never conversation
            # authority. A torn or older cache safely degrades to a fresh probe.
            return

    def _save(self) -> None:
        path = self._persist_path
        if path is None:
            return
        try:
            observations = []
            for (route, feature), value in sorted(
                self._entries.items(),
                key=lambda item: (
                    item[0][0].provider, item[0][0].endpoint_fingerprint,
                    item[0][0].model, item[0][0].api_version, item[0][1],
                ),
            ):
                observations.append({
                    "route": {
                        "provider": route.provider,
                        "endpoint_fingerprint": route.endpoint_fingerprint,
                        "model": route.model,
                        "api_version": route.api_version,
                    },
                    "feature": feature,
                    "state": value.state.value,
                    "observed_at": value.observed_at,
                    "expires_at": value.expires_at,
                    "last_error": value.last_error,
                    "attempts": value.attempts,
                })
            now = self._clock()

            def identity(item: Dict[str, Any]) -> tuple[str, ...]:
                route = dict(item.get("route") or {})
                return (
                    str(route.get("provider") or ""),
                    str(route.get("endpoint_fingerprint") or ""),
                    str(route.get("model") or ""),
                    str(route.get("api_version") or ""),
                    str(item.get("feature") or ""),
                )

            def merge(current: Any) -> Dict[str, Any]:
                durable = dict(current or {})
                existing = durable.get("observations") if durable.get("schema_version") == 1 else []
                combined = {
                    identity(item): item
                    for item in (existing or [])
                    if isinstance(item, dict)
                    and float(item.get("expires_at") or 0) > now
                }
                combined.update({identity(item): item for item in observations})
                return {
                    "schema_version": 1,
                    "observations": [combined[key] for key in sorted(combined)],
                }

            atomic_json_update(path, merge, default={})
        except (OSError, TypeError, ValueError):
            # Probe state must never make a model call fail.
            return

    def _current(
        self, route: CapabilityRoute, feature: str,
    ) -> Optional[CapabilityObservation]:
        key = (route, str(feature))
        observation = self._entries.get(key)
        if observation and observation.expires_at <= self._clock():
            self._entries.pop(key, None)
            self._expired.add(key)
            self._save()
            return None
        return observation

    def state(self, route: CapabilityRoute, feature: str) -> CapabilityState:
        current = self._current(route, feature)
        return current.state if current else CapabilityState.UNKNOWN

    def allows(self, route: CapabilityRoute, feature: str) -> bool:
        return self.state(route, feature) is not CapabilityState.REJECTED

    def consume_expired(self, route: CapabilityRoute, feature: str) -> bool:
        """Return once when a prior observation expired and the route may re-probe."""
        self._current(route, feature)
        key = (route, str(feature))
        if key not in self._expired:
            return False
        self._expired.remove(key)
        return True

    def observe(
        self,
        route: CapabilityRoute,
        feature: str,
        state: CapabilityState,
        error: Optional[BaseException | str] = None,
    ) -> CapabilityObservation:
        now = self._clock()
        previous = self._current(route, feature)
        ttl = (
            self.degraded_ttl_seconds
            if state is CapabilityState.DEGRADED else self.ttl_seconds
        )
        observation = CapabilityObservation(
            state=state,
            observed_at=now,
            expires_at=now + ttl,
            last_error=str(error) if error is not None else None,
            attempts=(previous.attempts if previous else 0) + 1,
        )
        self._entries[(route, str(feature))] = observation
        self._expired.discard((route, str(feature)))
        self._save()
        return observation

    def snapshot(
        self, route: CapabilityRoute, features: list[str],
    ) -> Dict[str, Dict[str, Any]]:
        now = self._clock()
        result: Dict[str, Dict[str, Any]] = {}
        for feature in features:
            observation = self._current(route, feature)
            result[feature] = (
                observation.as_dict(now)
                if observation else {"state": CapabilityState.UNKNOWN.value}
            )
        return result


__all__ = [
    "CapabilityObservation",
    "CapabilityRoute",
    "CapabilityState",
    "ProviderCapabilityRegistry",
]
