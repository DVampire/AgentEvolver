"""WebsiteUserAgent — a browser-only visitor that reports back what using the site is like."""

from typing import Any, Dict

from pydantic import Field

from agentevolver.agent.actor.browser_agent import BrowserAgent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class WebsiteUserAgent(BrowserAgent):
    """One registered template; every dispatch runs a fresh instance of it.

    Browser-only isolation is a runtime contract rather than a prompt suggestion: the
    allowlist it inherits leaves it exactly one tool, the one that ends the run, so it
    cannot reach the workspace it is supposed to be a visitor to.
    """

    name: str = Field(default="website_user_agent")
    description: str = Field(
        default="A browser-only website user that follows assigned user context, pursues "
        "realistic goals, and returns grounded co-design input."
    )
    metadata: Dict[str, Any] = Field(
        default={"role": "website_user", "browser_only": True}
    )
    prompt_name: str = Field(default="website_user_agent")


__all__ = ["WebsiteUserAgent"]
