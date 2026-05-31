"""AgentRegistrationHook — registers a generated agent class (and optional prompt) after done_tool fires."""

import os
from typing import Optional

from src.hook.types import Hook, HookContext, HookResult
from src.logger import logger
from src.registry import HOOK


@HOOK.register_module(force=True)
class AgentRegistrationHook(Hook):
    """Thin wrapper: resolves the agent file path from agent reasoning, then delegates to agent_manager.register()."""

    name: str = "agent_registration_hook"
    description: str = "Registers a generated agent class (and HTML prompt) with agent_manager after generation."
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        extra = ctx.input or {}
        target_name: Optional[str] = extra.get("target_name")
        reasoning: str = extra.get("reasoning") or ""
        project_root: str = extra.get("project_root") or ""
        model_name: str = extra.get("model_name") or ""

        py_path = self._resolve_agent_path(target_name, reasoning, project_root)
        if not py_path:
            msg = f"Could not locate generated agent file for '{target_name}' in reasoning."
            logger.warning(f"| ⚠️  AgentRegistrationHook: {msg}")
            return HookResult.block(f"[registration failed] {msg}\nInclude the file path in done_tool reasoning and call done_tool again.")

        try:
            from src.agent.server import agent_manager
            from src.dynamic import dynamic_manager
            with open(py_path, "r") as f:
                code = f.read()
            new_cls = dynamic_manager.load_class(code, context="agent")
            inferred_name = getattr(new_cls, "name", None) or target_name or new_cls.__name__
            existing = await agent_manager.get_info(inferred_name)
            if existing:
                await agent_manager.update(
                    agent_cls=new_cls,
                    agent_config_dict=existing.config or {},
                )
                logger.info(f"| 🔄 AgentRegistrationHook: '{inferred_name}' updated (re-registered)")
            else:
                await agent_manager.register(
                    agent_cls=new_cls,
                    agent_config_dict={"base_dir": os.path.join(project_root, "work_dir", inferred_name), "model_name": model_name, "require_grad": True},
                    override=True,
                )
                logger.info(f"| 🔄 AgentRegistrationHook: '{inferred_name}' registered from {py_path}")
        except Exception as e:
            logger.warning(f"| ⚠️  AgentRegistrationHook: {e}")
            return HookResult.block(f"[registration failed] {e}\nPlease fix the files and call done_tool again.")

        # Register HTML prompt if present (tool-calling agents) — non-fatal if fails
        html_path = os.path.join(project_root, "src", "prompt", "extended", f"{inferred_name}.html")
        if os.path.exists(html_path):
            try:
                from src.prompt import prompt_manager
                from src.prompt.types import parse_prompt_text
                with open(html_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                parsed = parse_prompt_text(html_content)
                if not parsed.name:
                    parsed = parsed.model_copy(update={"name": inferred_name})
                await prompt_manager.register(prompt=parsed.model_dump(), override=True)
                logger.info(f"| 🔄 AgentRegistrationHook: prompt '{inferred_name}' registered")
            except Exception as pe:
                logger.warning(f"| ⚠️  AgentRegistrationHook: prompt registration failed (non-fatal): {pe}")

        return HookResult.allow()

    def _resolve_agent_path(self, target_name: Optional[str], reasoning: str, project_root: str) -> Optional[str]:
        for token in reasoning.split():
            token = token.strip(".,;:()")
            if "src/agent/extended/" in token and token.endswith(".py"):
                candidate = token if token.startswith("/") else os.path.join(project_root, token)
                if os.path.exists(candidate):
                    return candidate
        if target_name and project_root:
            path = os.path.join(project_root, "src", "agent", "extended", f"{target_name}.py")
            return path if os.path.exists(path) else None
        return None
