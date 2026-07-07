"""skill manager Server — Skill Context Protocol.

Server implementation that mirrors the tool manager (Tool Context Protocol) pattern,
providing a unified interface for skill discovery, loading, registration,
update, and execution.
"""

import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.logger import logger
from src.config import config
from src.skill.context import SkillContextManager
from src.skill.types import SkillConfig, SkillContext
from src.response.types import Response, ResponseType
from src.session import SessionContext
from src.utils import assemble_project_path


class SkillManagerServer(BaseModel):
    """skill manager Server for managing skill registration and context generation."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default=None, description="Base directory for skill data")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.skill_context_manager: Optional[SkillContextManager] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_context_manager(self) -> SkillContextManager:
        """Lazily create the context manager so methods work before initialize() is called."""
        if self.skill_context_manager is None:
            self.skill_context_manager = SkillContextManager()
        return self.skill_context_manager

    async def initialize(self, skill_names: Optional[List[str]] = None):
        """Initialize skills by scanning default (and custom) skill directories.

        Args:
            skill_names: If provided, only these skills are loaded.
        """
        self.base_dir = assemble_project_path(os.path.join(config.run_dir, "skill"))
        os.makedirs(self.base_dir, exist_ok=True)
        logger.info(
            f"| 📁 skill manager Server base directory: {self.base_dir} "
        )

        self.skill_context_manager = SkillContextManager(
            base_dir=self.base_dir,
        )
        await self._ensure_context_manager().initialize(skill_names=skill_names)

        logger.info("| ✅ Skills initialization completed")

    async def cleanup(self):
        """Release all skills."""
        await self._ensure_context_manager().cleanup()

    # ------------------------------------------------------------------
    # Register / Update / Unregister / Copy / Restore
    # ------------------------------------------------------------------

    async def register(
        self,
        skill_dir: str,
        override: bool = False,
        version: Optional[str] = None,
        enable_evolving: Optional[bool] = None,
    ) -> SkillConfig:
        """Register a skill from a directory containing SKILL.md.

        Args:
            skill_dir: Path to the skill directory.
            override: If True, overwrite an existing skill with the same name.
            version: Explicit version string.
            enable_evolving: If not None, override the frontmatter-parsed evolvability flag.

        Returns:
            The registered SkillConfig.
        """
        return await self._ensure_context_manager().register(
            skill_dir=skill_dir,
            override=override,
            version=version,
            enable_evolving=enable_evolving,
        )

    async def update(
        self,
        name: str,
        skill_dir: Optional[str] = None,
        new_version: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SkillConfig:
        """Update an existing skill and create a new version.

        Args:
            name: Skill name.
            skill_dir: If provided, re-parse this directory.
            new_version: Explicit new version string.
            description: Override description.
            content: Override SKILL.md body content.
            metadata: Override metadata dict.

        Returns:
            Updated SkillConfig.
        """
        return await self._ensure_context_manager().update(
            name=name,
            skill_dir=skill_dir,
            new_version=new_version,
            description=description,
            content=content,
            metadata=metadata,
        )

    async def unregister(self, name: str) -> bool:
        """Remove a skill.

        Args:
            name: Skill name.

        Returns:
            True if removed, False if not found.
        """
        return await self._ensure_context_manager().unregister(name)

    async def copy(
        self,
        name: str,
        new_name: Optional[str] = None,
        new_version: Optional[str] = None,
        new_skill_dir: Optional[str] = None,
    ) -> SkillConfig:
        """Copy an existing skill, optionally under a new name.

        Args:
            name: Source skill name.
            new_name: Name for the copy.
            new_version: Version for the copy.
            new_skill_dir: If provided, physically copies the skill directory.

        Returns:
            New SkillConfig.
        """
        return await self._ensure_context_manager().copy(
            name=name,
            new_name=new_name,
            new_version=new_version,
            new_skill_dir=new_skill_dir,
        )

    async def restore(self, name: str, version: str) -> Optional[SkillConfig]:
        """Restore a specific version of a skill from history.

        Args:
            name: Skill name.
            version: Version string to restore.

        Returns:
            Restored SkillConfig, or None if not found.
        """
        return await self._ensure_context_manager().restore(name, version)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    async def get(self, skill_name: str) -> Optional[SkillConfig]:
        """Get a loaded skill by name."""
        return await self._ensure_context_manager().get(skill_name)

    async def get_info(self, skill_name: str) -> Optional[SkillConfig]:
        """Get skill configuration by name."""
        return await self._ensure_context_manager().get_info(skill_name)

    async def list(self) -> List[str]:
        """List all loaded skill names."""
        return await self._ensure_context_manager().list()

    # ------------------------------------------------------------------
    # Context & Contract
    # ------------------------------------------------------------------

    async def get_instruction(self, allowlist: Optional[List[str]] = None, types: Optional[List[str]] = None) -> str:
        """Assemble the skill instruction text for prompt injection.

        `allowlist` (skill names) selects which skills to include (None = all, [] = none).
        `types` filters by frontmatter type (["worker"] for sub-agents, ["orchestrator"]
        for the MetaAgent). Cached per (allowlist, types) until the registry changes.
        """
        return await self._ensure_context_manager().get_instruction(allowlist=allowlist, types=types)

    # ------------------------------------------------------------------
    # Skill execution
    # ------------------------------------------------------------------

    async def __call__(
        self,
        name: str,
        input: Dict[str, Any],
        ctx: SkillContext = None,
        **kwargs,
    ) -> Response:
        """Execute a skill by name.

        Args:
            name: Skill name.
            input: User-provided arguments.
            ctx: Skill context.
        """
        # Ensure ctx is always an SkillContext instance
        ctx = SkillContext.from_context(ctx) if ctx else SkillContext(name=name, input=input)

        return await self._ensure_context_manager()(
            name=name,
            input=input,
            ctx=ctx,
            **kwargs,
        )


# Global skill manager instance
skill_manager = SkillManagerServer()
