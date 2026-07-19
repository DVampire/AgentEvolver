"""EnvironmentRegistrationHook — registers a generated environment class after done_tool fires."""

import os
from typing import Optional

from agentevolver.hook.types import Hook, HookContext, HookResult
from agentevolver.logger import logger
from agentevolver.registry import HOOK


@HOOK.register_module(force=True)
class EnvironmentRegistrationHook(Hook):
    """Thin wrapper: resolves the environment file path from agent reasoning, then delegates to environment_manager."""

    name: str = "environment_registration_hook"
    description: str = "Registers a generated environment class with environment_manager after generation/optimization."
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        extra = ctx.input or {}
        target_name: Optional[str] = extra.get("target_name")
        reasoning: str = extra.get("reasoning") or ""
        extension_root: str = extra.get("extension_root") or ""

        py_path = self._resolve_environment_path(target_name, reasoning, extension_root)
        if not py_path:
            msg = f"Could not locate generated environment file for '{target_name}' in reasoning."
            logger.warning(f"| ⚠️  EnvironmentRegistrationHook: {msg}")
            return HookResult.block(
                f"[registration failed] {msg}\nInclude the file path (extension/<version>/environment/<name>.py) in done_tool reasoning and call done_tool again."
            )

        from agentevolver.sandbox.project import is_staged_extension_root, validate_staged_extension
        if is_staged_extension_root(extension_root):
            validate_staged_extension(extension_root)
            logger.info(f"| 📦 EnvironmentRegistrationHook: staged '{target_name or os.path.basename(py_path)}' for promotion")
            return HookResult.allow()

        try:
            from agentevolver.extension import extension_manager
            inferred_name = await extension_manager.add_component("environment", py_path, config={"enable_evolving": True})
            logger.info(f"| 🔄 EnvironmentRegistrationHook: '{inferred_name}' registered from {py_path}")
        except Exception as e:
            logger.warning(f"| ⚠️  EnvironmentRegistrationHook: {e}")
            return HookResult.block(f"[registration failed] {e}\nPlease fix the file and call done_tool again.")

        return HookResult.allow()

    def _resolve_environment_path(self, target_name: Optional[str], reasoning: str, extension_root: str) -> Optional[str]:
        from agentevolver.extension import extension_manager
        for token in reasoning.split():
            token = token.strip(".,;:()")
            if "extension/" in token and "/environment/" in token and token.endswith(".py"):
                candidate = token if token.startswith("/") else os.path.join(extension_root, token.removeprefix("extension/"))
                if os.path.exists(candidate):
                    return candidate
        if target_name:
            path = extension_manager.stage_path("environment", f"{target_name}.py")
            return path if os.path.exists(path) else None
        return None
