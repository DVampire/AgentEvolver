"""Reusable long-lived browser co-designer for participatory website releases."""

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
            "A browser-only website co-designer that follows one assigned user persona, "
            "attempts realistic goals, saves preferences, and contributes a desired "
            "experience through the website's own participatory interface."
        )
    )
    metadata: Dict[str, Any] = Field(
        default_factory=lambda: {"role": "website_user_codesigner", "browser_only": True}
    )
    subscription_topics: List[str] = Field(default_factory=lambda: ["website.releases"])

    def _required_capability_allowlists(self) -> Dict[str, List[str]]:
        """Make browser-only isolation a runtime contract, not a prompt suggestion."""
        return {
            "tool_allowlist": ["done_tool", "escalate_tool"],
            "skill_allowlist": [],
            "connector_allowlist": [],
            "plugin_allowlist": [],
            "environment_allowlist": [self.env_name],
            "workflow_allowlist": [],
        }

    def _reset_release_budget(self, ctx: Any) -> None:
        """Release-turn budgets reset; subscriber identity and memory do not."""
        context_id = str(getattr(ctx, "id", "") or "")
        if context_id:
            for constraint in self.constraints:
                constraint._cleanup(context_id)
        # One deep-copied WebsiteUserAgent serves one serialized subscriber, so no
        # unrelated run can own an entry in this instance-local map.
        self._pending_step_tokens.clear()

    async def on_subscription_event(self, msg, ref) -> None:
        """Start every release co-design turn with a clean browser context.

        The Agent subscription is intentionally long-lived (one user follows V0 → V1),
        while the browser measurement must not inherit cookies, local storage, tabs, or
        page state from the previous iteration. Closing the per-session browser context
        here preserves both properties.
        """
        ctx = (msg.kwargs or {}).get("ctx")
        environment = await environment_manager.get(self.env_name)
        if environment is not None and ctx is not None:
            await environment.close_session(str(getattr(ctx, "id", "") or "default"))
        self._observed_state = None
        try:
            await super().on_subscription_event(msg, ref)
        finally:
            self._reset_release_budget(ctx)
__all__ = ["WebsiteUserAgent"]
