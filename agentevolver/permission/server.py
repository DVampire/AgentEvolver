"""Permission manager server — global singleton, mirrors skill_manager pattern."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .context import PermissionContextManager
from .types import (
    PermissionEnforcer,
    PermissionMode,
    PermissionPolicy,
    PermissionRequest,
    ValidationResult,
)


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
        return self._ctx.check(name=name, input=input, **kwargs)

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
        support direct Python invocation in tests and small scripts. Outside a bound run
        there is no workspace boundary to infer, so ``workspace_write`` retains its
        historical standalone meaning while all command/destructive/size checks remain.
        """
        declared = PermissionMode(mode)
        from agentevolver.paths import path_manager

        roots = path_manager.session_roots()
        if declared is PermissionMode.WORKSPACE_WRITE and not workspace:
            if roots:
                workspace = str(roots["workspace"])
            else:
                declared = PermissionMode.DANGER_FULL_ACCESS
        return PermissionEnforcer(
            entity_name=name, mode=declared, workspace=workspace,
        ).check(input, workspace=workspace)


# Global singleton
permission_manager = PermissionManagerServer()
