"""Reusable browser-only user and co-designer for website tasks."""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.agent.actor.browser_agent import BrowserAgent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class WebsiteUserAgent(BrowserAgent):
    """One registered template; every dispatch receives a deep-copied runtime instance."""

    name: str = Field(default="website_user_agent")
    description: str = Field(
        default=(
            "A browser-only website user that follows assigned user context, pursues "
            "realistic goals, and returns grounded co-design input."
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

__all__ = ["WebsiteUserAgent"]
