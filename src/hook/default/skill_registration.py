"""SkillRegistrationHook — registers a generated skill directory after done_tool fires."""

import os
from typing import Optional

from src.hook.types import Hook, HookContext, HookResult
from src.logger import logger
from src.registry import HOOK


@HOOK.register_module(force=True)
class SkillRegistrationHook(Hook):
    """Thin wrapper: resolves the skill directory from agent reasoning, then delegates to skill_manager.register()."""

    name: str = "skill_registration_hook"
    description: str = "Registers a generated skill directory with skill_manager after generation."
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        extra = ctx.extra or {}
        target_name: Optional[str] = extra.get("target_name")
        reasoning: str = extra.get("reasoning") or ""
        project_root: str = extra.get("project_root") or ""

        skill_dir = self._resolve_skill_dir(target_name, reasoning, project_root)
        if not skill_dir:
            msg = f"Could not locate generated skill directory for '{target_name}' in reasoning."
            logger.warning(f"| ⚠️  SkillRegistrationHook: {msg}")
            return HookResult.block(f"[registration failed] {msg}\nInclude the skill directory path in done_tool reasoning and call done_tool again.")

        try:
            from src.skill.server import skill_manager
            await skill_manager.register(skill_dir=skill_dir, override=True)
            logger.info(f"| 🔄 SkillRegistrationHook: '{target_name}' registered from {skill_dir}")
            return HookResult.allow()
        except Exception as e:
            logger.warning(f"| ⚠️  SkillRegistrationHook: {e}")
            return HookResult.block(f"[registration failed] {e}\nPlease fix the issue and call done_tool again.")

    def _resolve_skill_dir(self, target_name: Optional[str], reasoning: str, project_root: str) -> Optional[str]:
        for token in reasoning.split():
            if "src/skill/extended/" in token:
                candidate = token.strip(".,;:()")
                if not candidate.startswith("/"):
                    candidate = os.path.join(project_root, candidate)
                if os.path.isdir(candidate.rstrip("/")):
                    return candidate.rstrip("/")
        if target_name and project_root:
            return os.path.join(project_root, "src", "skill", "extended", target_name)
        return None
