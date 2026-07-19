"""SkillRegistrationHook — registers a generated skill directory after done_tool fires."""

import os
from typing import Optional

from agentevolver.hook.types import Hook, HookContext, HookResult
from agentevolver.logger import logger
from agentevolver.registry import HOOK


@HOOK.register_module(force=True)
class SkillRegistrationHook(Hook):
    """Thin wrapper: resolves the skill directory from agent reasoning, then delegates to skill_manager.register()."""

    name: str = "skill_registration_hook"
    description: str = "Registers a generated skill directory with skill_manager after generation."
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        extra = ctx.input or {}
        target_name: Optional[str] = extra.get("target_name")
        reasoning: str = extra.get("reasoning") or ""
        extension_root: str = extra.get("extension_root") or ""

        skill_dir = self._resolve_skill_dir(target_name, reasoning, extension_root)
        if not skill_dir:
            msg = f"Could not locate generated skill directory for '{target_name}' in reasoning."
            logger.warning(f"| ⚠️  SkillRegistrationHook: {msg}")
            return HookResult.block(f"[registration failed] {msg}\nInclude the skill directory path in done_tool reasoning and call done_tool again.")

        from agentevolver.sandbox.project import is_staged_extension_root, validate_staged_extension
        if is_staged_extension_root(extension_root):
            validate_staged_extension(extension_root)
            logger.info(f"| 📦 SkillRegistrationHook: staged '{target_name or os.path.basename(skill_dir)}' for promotion")
            return HookResult.allow()

        try:
            from agentevolver.extension import extension_manager
            # Newly generated components are registered evolvable so a later round can optimize
            # them. Overwriting an existing *frozen* entity is still refused inside add_component.
            name = await extension_manager.add_component("skill", skill_dir, config={"enable_evolving": True})
            logger.info(f"| 🔄 SkillRegistrationHook: '{name}' registered from {skill_dir}")
            return HookResult.allow()
        except Exception as e:
            logger.warning(f"| ⚠️  SkillRegistrationHook: {e}")
            return HookResult.block(f"[registration failed] {e}\nPlease fix the issue and call done_tool again.")

    def _resolve_skill_dir(self, target_name: Optional[str], reasoning: str, extension_root: str) -> Optional[str]:
        from agentevolver.extension import extension_manager
        for token in reasoning.split():
            if "extension/" in token and "/skill/" in token:
                candidate = token.strip(".,;:()")
                if not candidate.startswith("/"):
                    candidate = os.path.join(extension_root, candidate.removeprefix("extension/"))
                if os.path.isdir(candidate.rstrip("/")):
                    return candidate.rstrip("/")
        if target_name:
            path = extension_manager.stage_path("skill", target_name)
            return path if os.path.isdir(path) else None
        return None
