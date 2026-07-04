"""Inspect a registered skill in-process and return a structured evaluation record."""
import re
from typing import Any, Dict, Optional
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL
from src.logger import logger

_DESCRIPTION = "Inspect a registered skill in-process and return a structured evaluation record (structure, completeness, resources, and quality signals)."

_INSTRUCTION = """
## Function
Inspect a registered skill and return a raw structured record of its SKILL.md structure and bundled resources — the objective half of a skill evaluation (pair it with your own qualitative judgement of clarity/usefulness).

## Guidance
- Use this during the evaluate/iterate phase instead of hand-parsing SKILL.md: it runs against the live skill registry.
- It does NOT execute the skill; it inspects the loaded SkillConfig (frontmatter + body + resource lists) and flags common structural issues.
- Judge clarity, trigger accuracy, and instruction quality yourself from the record + the SKILL.md body.

## Parameters
- name (str): the registered name of the skill to inspect (e.g. "my_skill").

## Example
{"name": "skill_eval_runner", "args": {"name": "my_skill"}}
"""


@TOOL.register_module(force=True)
class SkillEvalRunnerTool(Tool):
    """Inspect a registered skill and return a structured evaluation record."""

    name: str = "skill_eval_runner"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, name: str, **kwargs) -> Response:
        """Return a structured evaluation record for the named skill.

        Args:
            name (str): the registered name of the skill to inspect.
        """
        from src.skill.server import skill_manager

        cfg = await skill_manager.get_info(name)
        if cfg is None:
            available = await skill_manager.list()
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=f"Skill '{name}' not found. Available skills: {available}",
            )

        content = cfg.content or ""
        sections = re.findall(r"^#{1,6}\s+(.+?)\s*$", content, re.MULTILINE)

        description = (cfg.description or "").strip()
        issues = []
        if not description:
            issues.append("missing description")
        elif len(description) < 40:
            issues.append("description very short (weak triggering)")
        if len(content) < 200:
            issues.append("SKILL.md body very short")
        if not sections:
            issues.append("no markdown sections in body")
        for f in list(cfg.scripts) + list(cfg.references) + list(cfg.examples):
            import os
            if not os.path.exists(f):
                issues.append(f"referenced file missing: {f}")

        data = {
            "registered": True,
            "name": cfg.name,
            "version": cfg.version,
            "type": cfg.type,
            "has_description": bool(description),
            "description_length": len(description),
            "content_length": len(content),
            "num_sections": len(sections),
            "sections": sections,
            "num_scripts": len(cfg.scripts),
            "num_references": len(cfg.references),
            "num_examples": len(cfg.examples),
            "skill_dir": cfg.skill_dir,
            "issues": issues,
        }
        msg = (
            f"Skill '{cfg.name}' v{cfg.version}: {len(content)} chars, {len(sections)} sections, "
            f"{len(cfg.scripts)} script(s), {len(cfg.references)} reference(s). "
            f"Issues: {issues if issues else 'none'}"
        )
        return Response(type=ResponseType.TOOL, success=True, message=msg, data=data)
