"""ToolRegistrationHook — registers a generated tool file after done_tool fires."""

import os
from typing import Optional

from src.hook.types import Hook, HookContext, HookResult
from src.logger import logger
from src.registry import HOOK


@HOOK.register_module(force=True)
class ToolRegistrationHook(Hook):
    """Thin wrapper: resolves the tool file path from agent reasoning, then delegates to tool_manager.register()."""

    name: str = "tool_registration_hook"
    description: str = "Registers a generated tool class with tool_manager after generation."
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        extra = ctx.extra or {}
        target_name: Optional[str] = extra.get("target_name")
        reasoning: str = extra.get("reasoning") or ""
        project_root: str = extra.get("project_root") or ""

        tool_path = self._resolve_tool_path(target_name, reasoning, project_root)
        if not tool_path:
            msg = f"Could not locate generated tool file for '{target_name}' in reasoning."
            logger.warning(f"| ⚠️  ToolRegistrationHook: {msg}")
            return HookResult.block(f"[registration failed] {msg}\nInclude the file path in done_tool reasoning and call done_tool again.")

        try:
            from src.tool.server import tool_manager
            from src.dynamic import dynamic_manager
            with open(tool_path, "r") as f:
                code = f.read()
            new_cls = dynamic_manager.load_class(code, class_name=target_name, context="tool")
            existing = await tool_manager.get_info(target_name)
            if existing:
                await tool_manager.update(target_name=target_name, tool=new_cls, config=existing.config or {}, code=code)
                logger.info(f"| 🔄 ToolRegistrationHook: '{target_name}' updated (re-registered)")
            else:
                await tool_manager.register(tool=new_cls, config={}, code=code, override=True)
                logger.info(f"| 🔄 ToolRegistrationHook: '{target_name}' registered from {tool_path}")
            return HookResult.allow()
        except Exception as e:
            logger.warning(f"| ⚠️  ToolRegistrationHook: {e}")
            return HookResult.block(f"[registration failed] {e}\nPlease fix the code and call done_tool again.")

    def _resolve_tool_path(self, target_name: Optional[str], reasoning: str, project_root: str) -> Optional[str]:
        for token in reasoning.split():
            token = token.strip(".,;:()")
            if "src/tool/extended/" in token and token.endswith(".py"):
                candidate = token if token.startswith("/") else os.path.join(project_root, token)
                if os.path.exists(candidate):
                    return candidate
        if target_name and project_root:
            path = os.path.join(project_root, "src", "tool", "extended", f"{target_name}.py")
            return path if os.path.exists(path) else None
        return None
