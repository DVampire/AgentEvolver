"""Independent browser co-designers used by the website self-evolution demo.

The browser environment isolates pages by context id, but ``BrowserAgent`` also keeps
the latest observation on the Python instance while it renders a turn.  The demo runs
three co-designers concurrently, so each participant needs its own registered class (and
therefore its own instance) rather than three calls to one shared ``BrowserAgent``.
"""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.agent.actor.browser_agent import BrowserAgent
from agentevolver.environment.server import environment_manager
from agentevolver.registry import AGENT


class _WebsiteUserAgent(BrowserAgent):
    """Common implementation for a persona-grounded, browser-only co-designer."""

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


@AGENT.register_module(force=True)
class WebsiteUser1Agent(_WebsiteUserAgent):
    """First independent website user co-designer."""

    name: str = Field(default="website_user_1_agent")


@AGENT.register_module(force=True)
class WebsiteUser2Agent(_WebsiteUserAgent):
    """Second independent website user co-designer."""

    name: str = Field(default="website_user_2_agent")


@AGENT.register_module(force=True)
class WebsiteUser3Agent(_WebsiteUserAgent):
    """Third independent website user co-designer."""

    name: str = Field(default="website_user_3_agent")


__all__ = ["WebsiteUser1Agent", "WebsiteUser2Agent", "WebsiteUser3Agent"]
