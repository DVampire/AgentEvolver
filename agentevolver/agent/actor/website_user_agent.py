"""Reusable browser-only user and co-designer for website tasks."""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.agent.actor.browser_agent import BrowserAgent
from agentevolver.environment.server import environment_manager
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class WebsiteUserAgent(BrowserAgent):
    """One registered template; every dispatch receives a deep-copied runtime instance."""

    name: str = Field(default="website_user_agent")
    description: str = Field(
        default=(
            "A browser-only website user that follows assigned user context, attempts "
            "realistic goals, and returns grounded experience evidence or co-design input."
        )
    )
    metadata: Dict[str, Any] = Field(
        default_factory=lambda: {"role": "website_user", "browser_only": True}
    )

    def _required_capability_allowlists(self) -> Dict[str, List[str]]:
        """Make browser-only isolation a runtime contract, not a prompt suggestion."""
        return {
            "tool_allowlist": ["done_tool"],
            "skill_allowlist": [],
            "connector_allowlist": [],
            "plugin_allowlist": [],
            "environment_allowlist": [self.env_name],
            "workflow_allowlist": [],
        }

    def _reset_turn_budget(self, ctx: Any) -> None:
        """Per-task budgets reset; participant identity and memory do not."""
        context_id = str(getattr(ctx, "id", "") or "")
        if context_id:
            for constraint in self.constraints:
                constraint._cleanup(context_id)
        # One deep-copied WebsiteUserAgent serves one serialized participant, so no
        # unrelated run can own an entry in this instance-local map.
        self._pending_step_tokens.clear()

    async def on_start(self, task, files, ctx, ref, **kwargs):
        """Run each continuation in a fresh browser while preserving Agent memory."""
        environment = await environment_manager.get(self.env_name)
        if environment is not None:
            await environment.close_session(str(getattr(ctx, "id", "") or "default"))
        self._observed_state = None
        try:
            return await super().on_start(
                task=task, files=files, ctx=ctx, ref=ref, **kwargs,
            )
        finally:
            self._reset_turn_budget(ctx)
__all__ = ["WebsiteUserAgent"]
