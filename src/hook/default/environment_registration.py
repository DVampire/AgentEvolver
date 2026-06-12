"""EnvironmentRegistrationHook — registers a generated environment class after done_tool fires."""

import os
from typing import Optional

from src.hook.types import Hook, HookContext, HookResult
from src.logger import logger
from src.registry import HOOK


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
        project_root: str = extra.get("project_root") or ""

        py_path = self._resolve_environment_path(target_name, reasoning, project_root)
        if not py_path:
            msg = f"Could not locate generated environment file for '{target_name}' in reasoning."
            logger.warning(f"| ⚠️  EnvironmentRegistrationHook: {msg}")
            return HookResult.block(
                f"[registration failed] {msg}\nInclude the file path (src/environment/extended/<name>.py) in done_tool reasoning and call done_tool again."
            )

        try:
            from src.environment.server import environment_manager
            from src.dynamic import dynamic_manager
            with open(py_path, "r") as f:
                code = f.read()
            new_cls = dynamic_manager.load_class(code, context="environment")
            new_cls.__source_file__ = py_path
            inferred_name = new_cls.model_fields["name"].default if "name" in new_cls.model_fields else (target_name or new_cls.__name__)
            existing = await environment_manager.get_info(inferred_name)
            if existing:
                await environment_manager.update(
                    env_cls=new_cls,
                    env_config_dict=existing.config or {},
                )
                logger.info(f"| 🔄 EnvironmentRegistrationHook: '{inferred_name}' updated (re-registered)")
            else:
                await environment_manager.register(
                    env_cls=new_cls,
                    env_config_dict={"require_grad": True},
                    override=True,
                )
                logger.info(f"| 🔄 EnvironmentRegistrationHook: '{inferred_name}' registered from {py_path}")
        except Exception as e:
            logger.warning(f"| ⚠️  EnvironmentRegistrationHook: {e}")
            return HookResult.block(f"[registration failed] {e}\nPlease fix the file and call done_tool again.")

        return HookResult.allow()

    def _resolve_environment_path(self, target_name: Optional[str], reasoning: str, project_root: str) -> Optional[str]:
        for token in reasoning.split():
            token = token.strip(".,;:()")
            if "src/environment/extended/" in token and token.endswith(".py"):
                candidate = token if token.startswith("/") else os.path.join(project_root, token)
                if os.path.exists(candidate):
                    return candidate
        if target_name and project_root:
            path = os.path.join(project_root, "src", "environment", "extended", f"{target_name}.py")
            return path if os.path.exists(path) else None
        return None
