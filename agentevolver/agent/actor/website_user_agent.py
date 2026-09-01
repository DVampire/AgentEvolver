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
        await super().on_subscription_event(msg, ref)
__all__ = ["WebsiteUserAgent"]
