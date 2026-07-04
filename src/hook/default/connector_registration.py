"""ConnectorRegistrationHook — registers a generated connector directory after done_tool fires."""

import os
from typing import Optional

from src.hook.types import Hook, HookContext, HookResult
from src.logger import logger
from src.registry import HOOK


@HOOK.register_module(force=True)
class ConnectorRegistrationHook(Hook):
    """Thin wrapper: resolves the connector directory from agent reasoning, then delegates to connector_manager.register()."""

    name: str = "connector_registration_hook"
    description: str = "Registers a generated connector directory (CONNECTOR.md) with connector_manager after generation."
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        extra = ctx.input or {}
        target_name: Optional[str] = extra.get("target_name")
        reasoning: str = extra.get("reasoning") or ""
        project_root: str = extra.get("project_root") or ""

        connector_dir = self._resolve_connector_dir(target_name, reasoning, project_root)
        if not connector_dir:
            msg = f"Could not locate generated connector directory for '{target_name}' in reasoning."
            logger.warning(f"| ⚠️  ConnectorRegistrationHook: {msg}")
            return HookResult.block(f"[registration failed] {msg}\nInclude the connector directory path in done_tool reasoning and call done_tool again.")

        try:
            from src.extension import extension_manager
            name = await extension_manager.add_component("connector", connector_dir)
            logger.info(f"| 🔄 ConnectorRegistrationHook: '{name}' registered from {connector_dir}")
            return HookResult.allow()
        except Exception as e:
            logger.warning(f"| ⚠️  ConnectorRegistrationHook: {e}")
            return HookResult.block(f"[registration failed] {e}\nPlease fix the issue and call done_tool again.")

    def _resolve_connector_dir(self, target_name: Optional[str], reasoning: str, project_root: str) -> Optional[str]:
        from src.extension import extension_manager
        for token in reasoning.split():
            if "extension/" in token and "/connector/" in token:
                candidate = token.strip(".,;:()")
                if not candidate.startswith("/"):
                    candidate = os.path.join(project_root, candidate)
                if os.path.isdir(candidate.rstrip("/")):
                    return candidate.rstrip("/")
        if target_name:
            path = extension_manager.stage_path("connector", target_name)
            return path if os.path.isdir(path) else None
        return None
