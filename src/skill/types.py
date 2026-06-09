"""Skill type definitions for the Skill Context Protocol."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from src.session import BaseContext
from src.response.types import Response, ResponseType


class SkillContext(BaseContext):
    """Context passed into skill manager and individual skill instances."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(default="", description="Unique identifier for this skill invocation.")
    name: str = Field(default="", description="Name of the skill being invoked.")
    work_dir: Optional[str] = Field(default=None, description="Working directory available to the skill.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload passed to the skill.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this skill context.")


class SkillConfig(BaseModel):
    """Configuration for a loaded skill, parsed from SKILL.md and its directory."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="Skill name from YAML frontmatter")
    description: str = Field(description="Skill description from YAML frontmatter")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional YAML frontmatter fields")
    require_grad: bool = Field(default=False, description="Whether the skill is trainable")
    permission_mode: str = Field(default="workspace_write", description="Permission mode: read_only / workspace_write / danger_full_access")
    version: str = Field(default="1.0.0", description="Version of the skill")
    skill_type: str = Field(default="tool", description="Skill type: 'sop' (returns instructions) or 'tool' (LLM executes)")

    skill_dir: str = Field(description="Absolute path to the skill directory")
    content: str = Field(default="", description="Full markdown body of SKILL.md (after frontmatter)")
    scripts: List[str] = Field(default_factory=list, description="Paths to files under scripts/")
    resources: List[str] = Field(default_factory=list, description="Paths to files under resources/")
    references: List[str] = Field(default_factory=list, description="Paths to reference docs under references/")
    examples: List[str] = Field(default_factory=list, description="Paths to example files under examples/")

    text: Optional[str] = Field(default=None, description="Pre-built text representation for prompt injection")

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "metadata": self.metadata,
            "require_grad": self.require_grad,
            "permission_mode": self.permission_mode,
            "version": self.version,
            "skill_type": self.skill_type,
            "skill_dir": self.skill_dir,
            "content": self.content,
            "scripts": self.scripts,
            "resources": self.resources,
            "references": self.references,
            "examples": self.examples,
            "text": self.text,
        }


__all__ = [
    "SkillConfig",
    "SkillContext",
]
