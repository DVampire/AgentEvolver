"""ComputerAgent — drives a full desktop (the ``computer`` environment).

Computer-use and browsing are the same perception–action loop: observe a
screenshot (+ Set-of-Marks elements), decide, act with mouse/keyboard. So this
agent reuses :class:`BrowserAgent`'s loop wholesale — screenshot attachment,
environment-action dispatch, environment-context injection — and only re-points
it at the desktop environment (and its own prompt). It differs from the browser
agent exactly as the environments do: a whole desktop instead of one web page.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from agentevolver.agent.actor.browser_agent import BrowserAgent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class ComputerAgent(BrowserAgent):
    """Operates a desktop (click/type/keys/scroll) via the ``computer`` environment."""

    name: str = "computer_agent"
    description: str = (
        "A computer-use agent that operates a full Linux desktop with mouse and keyboard "
        "— opening and driving any GUI application through the computer environment."
    )

    def __init__(
        self,
        base_dir: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        prompt_name: Optional[str] = None,
        memory_name: Optional[str] = None,
        env_name: str = "computer_environment",
        max_actions: int = 3,
        max_step: int = 40,
        max_screenshots: int = 2,
        review_steps: int = 5,
        enable_evolving: bool = False,
        **kwargs,
    ):
        super().__init__(
            base_dir=base_dir,
            name=name,
            description=description,
            metadata=metadata,
            model_name=model_name,
            prompt_name=prompt_name or "computer_agent",
            memory_name=memory_name,
            env_name=env_name,
            max_actions=max_actions,
            max_step=max_step,
            max_screenshots=max_screenshots,
            review_steps=review_steps,
            enable_evolving=enable_evolving,
            **kwargs,
        )
