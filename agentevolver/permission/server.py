"""Permission manager server — global singleton, mirrors skill_manager pattern."""
from __future__ import annotations

from typing import Optional
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict

from .context import PermissionContextManager
from .types import (
    PermissionEnforcer,
    PermissionMode,
    PermissionPolicy,
    PermissionRequest,
    ValidationResult,
)

# Coroutine-local authority, inherited by spawned tasks, never stored on a shared tool.
_SCOPES: ContextVar[tuple] = ContextVar("permission_scopes", default=())


class PermissionManagerServer(BaseModel):
    """Global permission manager — thin facade over PermissionContextManager."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ctx = PermissionContextManager()

    # ------------------------------------------------------------------
    # Registration (called by ContextManagers at entity build time)
    # ------------------------------------------------------------------

    def register(
        self,
        entity_name: str,
        mode: PermissionMode = PermissionMode.WORKSPACE_WRITE,
        workspace: str = "",
        policy: Optional[PermissionPolicy] = None,
        override: bool = True,
    ) -> None:
        """Register an entity with its permission mode and optional policy."""
        self._ctx.register(
            entity_name=entity_name,
            mode=mode,
            workspace=workspace,
            policy=policy,
            override=override,
        )

    def unregister(self, entity_name: str) -> None:
        """Remove the enforcer for an entity."""
        self._ctx.unregister(entity_name)

    def is_registered(self, entity_name: str) -> bool:
        return self._ctx.is_registered(entity_name)

    def get_mode(self, entity_name: str) -> Optional[PermissionMode]:
        return self._ctx.get_mode(entity_name)

    def restrict(self, *modes) -> PermissionMode:
        """Intersect declared permissions with the current execution's ceilings."""
        order = list(PermissionMode)
        values = [PermissionMode(mode) for mode in modes if mode is not None]
        values.extend(mode for mode, _ in _SCOPES.get())
        return min(values or [PermissionMode.DANGER_FULL_ACCESS], key=order.index)

    @contextmanager
    def scope(self, mode, *, workspace: str = ""):
        """Narrow one execution without changing any global entity registration."""
        from agentevolver.paths import path_manager

        if not workspace:
            roots = path_manager.session_roots()
            workspace = str(roots["workspace"]) if roots else ""
        entry = (PermissionMode(mode), workspace)
        scopes = _SCOPES.get()
        token = _SCOPES.set(scopes if entry in scopes else (*scopes, entry))
        try:
            yield
        finally:
            _SCOPES.reset(token)

    @staticmethod
    def _ceiling(name, request, result):
        if not result.allowed:
            return result
        for mode, workspace in _SCOPES.get():
            verdict = PermissionEnforcer(entity_name=name, mode=mode, workspace=workspace).check(
                request, workspace=workspace,
            )
            if not verdict.allowed:
                return verdict
            if verdict.requires_approval:
                result = verdict
        return result

    @contextmanager
    def relocate(self, source: str, target: str):
        """Host-only mapping for a dispatcher-created worktree; modes never widen."""
        from pathlib import Path

        source, target = str(Path(source).resolve()), str(Path(target).resolve())
        scopes = tuple((mode, target if workspace and str(Path(workspace).resolve()) == source else workspace)
                       for mode, workspace in _SCOPES.get())
        token = _SCOPES.set(scopes)
        try:
            yield
        finally:
            _SCOPES.reset(token)

    # ------------------------------------------------------------------
    # Unified check — the only surface tools/agents/skills call
    # ------------------------------------------------------------------

    def check(
        self,
        name: str,
        input: PermissionRequest,
        **kwargs,
    ) -> ValidationResult:
        """Check whether an operation is permitted for the named entity.

        Args:
            name:   Entity name (tool / agent / skill name).
            input:  PermissionRequest(op=..., target=..., content=...).
            **kwargs: Reserved for future context propagation.

        Returns:
            ValidationResult — check .allowed, .reason, .warning.

        Example::

            result = permission_manager.check(
                "bash_tool",
                PermissionRequest(op=Operation.BASH, target="rm -rf /tmp/foo"),
            )
            if not result.allowed:
                return Response(type=ResponseType.TOOL, success=False, message=result.reason)
        """
        return self._ceiling(name, input, self._ctx.check(name=name, input=input, **kwargs))

    def check_declared(
        self,
        name: str,
        input: PermissionRequest,
        *,
        mode: PermissionMode | str,
        workspace: str = "",
    ) -> ValidationResult:
        """Check a directly-instantiated entity against its own declared mode.

        Registry execution always uses :meth:`check` and remains fail-closed for an
        unknown name. This seam exists for built-in tools/environments that deliberately
        support direct Python invocation in tests and small scripts. A missing session
        never upgrades the entity to full access: the configured workspace is the final
        fallback, and an absent fence remains visibly ``workspace_write``.
        """
        declared = PermissionMode(mode)
        from agentevolver.paths import path_manager

        roots = path_manager.session_roots()
        if declared is PermissionMode.WORKSPACE_WRITE and not workspace:
            if roots:
                workspace = str(roots["workspace"])
            else:
                try:
                    from agentevolver.config import config

                    workspace = str(getattr(config, "workspace_root", "") or "")
                except Exception:  # noqa: BLE001 - optional standalone config
                    workspace = ""
        result = PermissionEnforcer(
            entity_name=name, mode=declared, workspace=workspace,
        ).check(input, workspace=workspace)
        return self._ceiling(name, input, result)


# Global singleton
permission_manager = PermissionManagerServer()
