"""Register generated/evolved Workflow HTML through the extension lifecycle."""

import os
from typing import Optional

from agentevolver.hook.types import Hook, HookContext, HookResult
from agentevolver.logger import logger
from agentevolver.registry import HOOK


@HOOK.register_module(force=True)
class WorkflowRegistrationHook(Hook):
    """Resolve one staged HTML artifact and register it as a live extension."""

    name: str = "workflow_registration_hook"
    description: str = "Validates and registers generated Workflow HTML as an active extension."
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        extra = ctx.input or {}
        path = self._resolve(
            extra.get("target_name"), extra.get("reasoning") or "", extra.get("extension_root") or "",
        )
        if not path:
            return HookResult.block(
                "[registration failed] Could not locate the generated Workflow HTML. "
                "Include its absolute extension/workflow/*.html path in done_tool reasoning."
            )
        try:
            # Match Tool/Skill evolution: a validated registration is live immediately.
            from lxml import html
            tree = html.fromstring(open(path, encoding="utf-8").read())
            node = tree if tree.tag == "workflow" else tree.find(".//workflow")
            if node is None:
                raise ValueError("HTML must contain a <workflow> element")
            node.set("status", "active")
            node.set("enable-evolving", "true")
            source = html.tostring(tree, encoding="unicode", pretty_print=True)
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(source)

            from agentevolver.sandbox.project import is_staged_extension_root, validate_staged_extension
            if is_staged_extension_root(extra.get("extension_root") or ""):
                validate_staged_extension(extra["extension_root"])
                from agentevolver.hook.promotion import promote_approved_component
                path = promote_approved_component(extra["extension_root"], path)

            from agentevolver.extension import extension_manager
            name = await extension_manager.add_component("workflow", path)
            logger.info(f"| 🔄 WorkflowRegistrationHook: registered active '{name}' from {path}")
            return HookResult.allow()
        except Exception as exc:
            logger.warning(f"| ⚠️ WorkflowRegistrationHook: {exc}")
            return HookResult.block(f"[registration failed] {exc}")

    @staticmethod
    def _resolve(target_name: Optional[str], reasoning: str, extension_root: str) -> Optional[str]:
        for token in reasoning.split():
            candidate = token.strip("`'\".,;:()")
            if candidate.endswith(".html") and "workflow" in candidate:
                if not os.path.isabs(candidate):
                    candidate = os.path.join(extension_root, candidate.removeprefix("extension/"))
                if os.path.isfile(candidate):
                    return candidate
        if target_name:
            from agentevolver.extension import extension_manager
            candidate = extension_manager.stage_path("workflow", f"{target_name}.html")
            return candidate if os.path.isfile(candidate) else None
        return None
