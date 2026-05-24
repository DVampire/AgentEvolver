from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from src.utils import make_id


def _workspace_fingerprint(workspace: str) -> str:
    """Deterministic FNV-1a-style fingerprint of the canonical workspace path."""
    canonical = os.path.realpath(workspace) if workspace else ""
    return hashlib.md5(canonical.encode()).hexdigest()[:16]


class SessionContext(BaseModel):
    """Top-level session identifier passed between all managers."""
    id: str = Field(
        default_factory=lambda: make_id(),
        description="Unique identifier for this session.",
    )
    workspace: str = Field(default="", description="Canonical workspace root path.")
    workspace_fingerprint: str = Field(
        default="",
        description="MD5 prefix of realpath(workspace) for namespace isolation.",
    )
    parent_session_id: Optional[str] = Field(
        default=None,
        description="ID of the session this one was forked from (agent sub-tasks).",
    )
    fork_branch: Optional[str] = Field(
        default=None,
        description="Git branch name at fork time, for lineage tracking.",
    )
    extra: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary transient data any module can attach to the session.",
    )

    @classmethod
    def create(
        cls,
        workspace: str = "",
        parent_id: Optional[str] = None,
        fork_branch: Optional[str] = None,
    ) -> "SessionContext":
        """Factory: creates a session with workspace fingerprinting."""
        return cls(
            workspace=workspace,
            workspace_fingerprint=_workspace_fingerprint(workspace),
            parent_session_id=parent_id,
            fork_branch=fork_branch,
        )


class BaseContext(BaseModel):
    """Base class for all module-level contexts."""
    id: str = Field(description="Task/execution ID — unique per agent run, used as hook isolation key and HTML file name.")

    @classmethod
    def from_session(cls, ctx: "SessionContext", **kwargs) -> "BaseContext":
        return cls(id=ctx.id, **kwargs)
