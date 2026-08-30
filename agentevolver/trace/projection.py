"""Durable, versioned watermarks for incremental Trace projections.

A projection may update a search index, metrics table, trajectory dataset, or UI cache.
Its output has a different schema lifecycle from the source Trace.  The watermark binds
those two facts — projector name/version and last committed source sequence — and moves
forward only after the consumer has durably written its own derived state.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Dict, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.paths import P, path_manager
from agentevolver.trace.types import TRACE_FORMAT_VERSION


PROJECTION_WATERMARK_VERSION = 1


class ProjectionWatermarkError(RuntimeError):
    """A stored cursor cannot be interpreted or advanced safely."""


class ProjectionVersionMismatch(ProjectionWatermarkError):
    """Derived state belongs to a different projector implementation version."""


class ProjectionRegistrationError(RuntimeError):
    """A projection name is missing, duplicated, or resolves to the wrong runner."""


@runtime_checkable
class ProjectionRunner(Protocol):
    """Runtime contract shared by every incremental Trace consumer.

    A runner owns its derived-state format, while the registry owns discovery and
    identity. Keeping those responsibilities separate lets a compact metrics checkpoint
    and an append-only trajectory reducer use different storage without inventing two
    unrelated ways for callers to locate and execute them.
    """

    projection_name: str
    projection_version: int

    def project(self, session_id: str, **kwargs: Any) -> Any: ...
    def reset(self, session_id: str) -> None: ...


ProjectionFactory = Callable[[Any, str], ProjectionRunner]


@dataclass(frozen=True)
class ProjectionRegistration:
    name: str
    version: int
    factory: ProjectionFactory


class ProjectionRegistry:
    """Versioned factory registry for durable Trace projections.

    Registration fails closed on duplicate names. A projector upgrade must replace the
    registration explicitly *and* bump its version; existing watermarks then force an
    explicit rebuild instead of letting new code reinterpret old derived state.
    """

    def __init__(self) -> None:
        self._registrations: Dict[str, ProjectionRegistration] = {}
        self._lock = RLock()

    def register(
        self,
        name: str,
        version: int,
        factory: ProjectionFactory,
        *,
        replace: bool = False,
    ) -> ProjectionRegistration:
        normalized = str(name).strip()
        if not normalized:
            raise ProjectionRegistrationError("projection name cannot be empty")
        if int(version) < 1:
            raise ProjectionRegistrationError("projection version must be positive")
        registration = ProjectionRegistration(normalized, int(version), factory)
        with self._lock:
            existing = self._registrations.get(normalized)
            if existing is not None and not replace:
                raise ProjectionRegistrationError(
                    f"projection {normalized!r} is already registered at version "
                    f"{existing.version}"
                )
            if existing is not None and replace and existing.version == int(version):
                raise ProjectionRegistrationError(
                    f"replacement for projection {normalized!r} must bump version "
                    f"above {existing.version}"
                )
            self._registrations[normalized] = registration
        return registration

    def registration(self, name: str) -> ProjectionRegistration:
        with self._lock:
            registration = self._registrations.get(str(name))
        if registration is None:
            raise ProjectionRegistrationError(f"projection {name!r} is not registered")
        return registration

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._registrations))

    def create(self, name: str, trace_reader: Any, trace_root: str) -> ProjectionRunner:
        registration = self.registration(name)
        runner = registration.factory(trace_reader, str(trace_root))
        if (
            getattr(runner, "projection_name", None) != registration.name
            or int(getattr(runner, "projection_version", 0)) != registration.version
        ):
            raise ProjectionRegistrationError(
                f"projection factory {registration.name!r} returned a runner with "
                "different name/version"
            )
        return runner

    def project(
        self,
        name: str,
        trace_reader: Any,
        trace_root: str,
        session_id: str,
        **kwargs: Any,
    ) -> Any:
        return self.create(name, trace_reader, trace_root).project(session_id, **kwargs)


_default_registry: Optional[ProjectionRegistry] = None
_default_registry_lock = RLock()


def get_default_projection_registry() -> ProjectionRegistry:
    """Return built-in projections without import-time registration side effects."""
    global _default_registry
    with _default_registry_lock:
        if _default_registry is None:
            # Imports are deliberately lazy: trajectory depends on Trace types and must
            # not create a package-level cycle merely to participate in discovery.
            from agentevolver.trace.stats import (
                PROJECTION_NAME as STATS_NAME,
                PROJECTOR_VERSION as STATS_VERSION,
                TraceStatsProjector,
            )
            from agentevolver.trajectory.projector import (
                PROJECTION_NAME as TRAJECTORY_NAME,
                PROJECTOR_VERSION as TRAJECTORY_VERSION,
                IncrementalTrajectoryProjector,
            )

            registry = ProjectionRegistry()
            registry.register(STATS_NAME, STATS_VERSION, TraceStatsProjector)
            registry.register(
                TRAJECTORY_NAME, TRAJECTORY_VERSION, IncrementalTrajectoryProjector,
            )
            _default_registry = registry
        return _default_registry


class ProjectionWatermark(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=PROJECTION_WATERMARK_VERSION)
    projection: str
    projection_version: int
    session_id: str
    last_seq: int = -1
    source_trace_format_version: int = Field(default=TRACE_FORMAT_VERSION)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _path_component(value: str) -> str:
    readable = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)[:48]
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{readable or 'unnamed'}-{digest}"


class ProjectionWatermarkStore:
    """Atomically persist one monotonic cursor per projection and session."""

    def __init__(self, trace_root: str) -> None:
        self.trace_root = str(trace_root)
        self.root = str(path_manager.under(trace_root, P.TRACE_PROJECTIONS))

    def path(self, projection: str, session_id: str) -> str:
        return str(path_manager.under(
            self.trace_root,
            P.TRACE_PROJECTION_WATERMARK,
            projection=_path_component(projection),
            filename=f"{_path_component(session_id)}.watermark.json",
        ))

    def load(
        self, projection: str, projection_version: int, session_id: str
    ) -> Optional[ProjectionWatermark]:
        path = self.path(projection, session_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            watermark = ProjectionWatermark.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 - corrupt cursors must never be ignored
            raise ProjectionWatermarkError(f"cannot read projection watermark {path}: {exc}") from exc
        if watermark.schema_version != PROJECTION_WATERMARK_VERSION:
            raise ProjectionWatermarkError(
                f"watermark schema {watermark.schema_version} is unsupported; "
                f"reader supports {PROJECTION_WATERMARK_VERSION}"
            )
        if watermark.projection != projection or watermark.session_id != session_id:
            raise ProjectionWatermarkError("projection watermark identity does not match its path")
        if watermark.projection_version != int(projection_version):
            raise ProjectionVersionMismatch(
                f"projection {projection!r} changed from version "
                f"{watermark.projection_version} to {projection_version}; rebuild its derived state"
            )
        if watermark.source_trace_format_version > TRACE_FORMAT_VERSION:
            raise ProjectionWatermarkError(
                f"projection consumed future trace format {watermark.source_trace_format_version}"
            )
        return watermark

    def advance(
        self,
        projection: str,
        projection_version: int,
        session_id: str,
        last_seq: int,
    ) -> ProjectionWatermark:
        """Commit ``last_seq`` atomically, refusing regressions and version drift."""
        current = self.load(projection, projection_version, session_id)
        if current is not None and int(last_seq) < current.last_seq:
            raise ProjectionWatermarkError(
                f"watermark regression {current.last_seq} -> {last_seq} is not allowed"
            )
        watermark = ProjectionWatermark(
            projection=projection,
            projection_version=int(projection_version),
            session_id=session_id,
            last_seq=int(last_seq),
        )
        path = self.path(projection, session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = path + f".tmp-{os.getpid()}-{uuid.uuid4().hex}"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(
                    watermark.model_dump(mode="json"), handle,
                    ensure_ascii=False, sort_keys=True, indent=2,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            # fsync the directory as well as the file. Without it, the renamed file is
            # atomic to readers but may disappear after a power loss on some filesystems.
            directory_fd = os.open(os.path.dirname(path), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        return watermark

    def after_seq(self, projection: str, projection_version: int, session_id: str) -> int:
        watermark = self.load(projection, projection_version, session_id)
        return watermark.last_seq if watermark is not None else -1

    def reset(self, projection: str, session_id: str) -> None:
        """Remove one cursor when a caller has explicitly chosen to rebuild."""
        path = self.path(projection, session_id)
        if os.path.exists(path):
            os.remove(path)


__all__ = [
    "PROJECTION_WATERMARK_VERSION",
    "ProjectionWatermark",
    "ProjectionWatermarkError",
    "ProjectionVersionMismatch",
    "ProjectionRegistrationError",
    "ProjectionRunner",
    "ProjectionRegistration",
    "ProjectionRegistry",
    "get_default_projection_registry",
    "ProjectionWatermarkStore",
]
