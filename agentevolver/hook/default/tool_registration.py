"""ToolRegistrationHook — registers a generated tool file after done_tool fires."""

import os
from typing import Optional

from agentevolver.hook.types import Hook, HookContext, HookResult
from agentevolver.logger import logger
from agentevolver.registry import HOOK


@HOOK.register_module(force=True)
class ToolRegistrationHook(Hook):
    """Thin wrapper: resolves the tool file path from agent reasoning, then delegates to tool_manager.register()."""

    name: str = "tool_registration_hook"
    description: str = "Registers a generated tool class with tool_manager after generation."
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        extra = ctx.input or {}
        target_name: Optional[str] = extra.get("target_name")
        reasoning: str = extra.get("reasoning") or ""
        extension_root: str = extra.get("extension_root") or ""

        tool_path = self._resolve_tool_path(target_name, reasoning, extension_root)
        if not tool_path:
            msg = f"Could not locate generated tool file for '{target_name}' in reasoning."
            logger.warning(f"| ⚠️  ToolRegistrationHook: {msg}")
            return HookResult.block(f"[registration failed] {msg}\nInclude the file path in done_tool reasoning and call done_tool again.")

        from agentevolver.sandbox.project import is_staged_extension_root, validate_staged_extension
        if is_staged_extension_root(extension_root):
            validate_staged_extension(extension_root)
            from agentevolver.hook.promotion import promote_approved_component
            tool_path = promote_approved_component(extension_root, tool_path)

        try:
            from agentevolver.extension import extension_manager
            # Newly generated components are registered evolvable so a later round can optimize
            # them. Overwriting an existing *frozen* entity is still refused inside add_component.
            name = await extension_manager.add_component("tool", tool_path, config={"enable_evolving": True})
            logger.info(f"| 🔄 ToolRegistrationHook: '{name}' promoted and registered from {tool_path}")
            return HookResult.allow()
        except Exception as e:
            logger.warning(f"| ⚠️  ToolRegistrationHook: {e}")
            return HookResult.block(f"[registration failed] {e}\nPlease fix the code and call done_tool again.")

    def _resolve_tool_path(self, target_name: Optional[str], reasoning: str, extension_root: str) -> Optional[str]:
        from agentevolver.extension import extension_manager
        for token in reasoning.split():
            token = token.strip(".,;:()")
            if "extension/" in token and "/tool/" in token and token.endswith(".py"):
                candidate = token if token.startswith("/") else os.path.join(extension_root, token.removeprefix("extension/"))
                if os.path.exists(candidate):
                    return candidate
        if target_name:
            path = extension_manager.stage_path("tool", f"{target_name}.py")
            return path if os.path.exists(path) else None
        return None
