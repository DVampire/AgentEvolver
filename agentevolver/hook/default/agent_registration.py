"""AgentRegistrationHook — registers a generated agent class (and optional prompt) after done_tool fires."""

import os
from typing import Optional

from agentevolver.hook.types import Hook, HookContext, HookResult
from agentevolver.logger import logger
from agentevolver.registry import HOOK


@HOOK.register_module(force=True)
class AgentRegistrationHook(Hook):
    """Thin wrapper: resolves the agent file path from agent reasoning, then delegates to agent_manager.register()."""

    name: str = "agent_registration_hook"
    description: str = "Registers a generated agent class (and HTML prompt) with agent_manager after generation."
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        """Locate the generated agent ``.py`` file, promote it if staged, and register it.

        Fired after an agent-generating run calls ``done_tool``. Resolves the
        target agent file from the payload, validates and promotes it out of any
        staged extension root, then registers it (and a sibling HTML prompt, if
        present) with ``extension_manager``. Registration failures are surfaced
        as a BLOCK so the calling agent can fix the files and retry.

        Args:
            ctx: Hook context whose ``input`` carries ``target_name``,
                ``reasoning``, ``extension_root`` and ``model_name``.

        Returns:
            ``HookResult.allow()`` on success, or ``HookResult.block(reason)``
            when the file cannot be located or registration fails.
        """
        extra = ctx.input or {}
        target_name: Optional[str] = extra.get("target_name")
        reasoning: str = extra.get("reasoning") or ""
        extension_root: str = extra.get("extension_root") or ""
        model_name: str = extra.get("model_name") or ""

        py_path = self._resolve_agent_path(target_name, reasoning, extension_root)
        if not py_path:
            msg = f"Could not locate generated agent file for '{target_name}' in reasoning."
            logger.warning(f"| ⚠️  AgentRegistrationHook: {msg}")
            return HookResult.block(f"[registration failed] {msg}\nInclude the file path in done_tool reasoning and call done_tool again.")

        from agentevolver.sandbox.project import is_staged_extension_root, validate_staged_extension
        if is_staged_extension_root(extension_root):
            validate_staged_extension(extension_root)
            from agentevolver.hook.promotion import promote_approved_component
            py_path = promote_approved_component(extension_root, py_path)

        try:
            from agentevolver.extension import extension_manager
            from agentevolver.config import config
            agent_config = {
                "base_dir": config.workspace_root,
                "model_name": model_name,
                "enable_evolving": True,
            }
            inferred_name = await extension_manager.add_component("agent", py_path, config=agent_config)
            logger.info(f"| 🔄 AgentRegistrationHook: '{inferred_name}' promoted and registered from {py_path}")
        except Exception as e:
            logger.warning(f"| ⚠️  AgentRegistrationHook: {e}")
            return HookResult.block(f"[registration failed] {e}\nPlease fix the files and call done_tool again.")

        # Register HTML prompt if present (tool-calling agents) — non-fatal if fails
        from agentevolver.extension import extension_manager
        html_path = extension_manager.stage_path("prompt", f"{inferred_name}.html")
        if os.path.exists(html_path):
            try:
                await extension_manager.add_component("prompt", html_path)
                logger.info(f"| 🔄 AgentRegistrationHook: prompt '{inferred_name}' registered")
            except Exception as pe:
                logger.warning(f"| ⚠️  AgentRegistrationHook: prompt registration failed (non-fatal): {pe}")

        return HookResult.allow()

    def _resolve_agent_path(self, target_name: Optional[str], reasoning: str, extension_root: str) -> Optional[str]:
        """Find the generated agent ``.py`` file referenced by the run.

        Prefers an ``extension/.../agent/*.py`` path mentioned in the agent's
        reasoning; falls back to the staged path derived from ``target_name``.

        Returns:
            The existing agent file path, or ``None`` if nothing resolvable exists.
        """
        from agentevolver.extension import extension_manager
        for token in reasoning.split():
            token = token.strip(".,;:()")
            if "extension/" in token and "/agent/" in token and token.endswith(".py"):
                candidate = token if token.startswith("/") else os.path.join(extension_root, token.removeprefix("extension/"))
                if os.path.exists(candidate):
                    return candidate
        if target_name:
            path = extension_manager.stage_path("agent", f"{target_name}.py")
            return path if os.path.exists(path) else None
        return None
