"""ComputerAgent — the same observe-act loop, pointed at a desktop instead of a page."""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.agent.actor.browser_agent import BrowserAgent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class ComputerAgent(BrowserAgent):
    """Operates a full Linux desktop — mouse, keyboard, any GUI application.

    Declaration only. Driving a desktop and driving a browser are the same loop over a
    different environment, and the screenshots, the failure tally and the session
    handling are all inherited.
    """

    name: str = Field(default="computer_agent")
    description: str = Field(
        default="A computer-use agent that operates a full Linux desktop with mouse and "
        "keyboard — opening and driving any GUI application through the computer "
        "environment."
    )
    metadata: Dict[str, Any] = Field(default={})
    prompt_name: str = Field(default="computer_agent")
    env_names: List[str] = Field(default=["computer_environment"])


__all__ = ["ComputerAgent"]
