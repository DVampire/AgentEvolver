"""Dedicated orchestrator for the participatory website evolution workflow."""

from typing import Any, Dict, Optional

from pydantic import Field

from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class WebsiteBuilderAgent(MetaAgent):
    """Website product builder with MetaAgent's general orchestration lifecycle.

    This is a distinct registered Agent rather than a renamed MetaAgent configuration:
    it has its own runtime identity, version history, prompt default, permissions, memory,
    traces, and evolution target.  It inherits only the reusable orchestration mechanics.
    """

    name: str = Field(default="website_builder_agent")
    description: str = Field(
        default=(
            "An evolvable website product engineer that builds and deploys releases, "
            "coordinates persistent user co-designers, delivers personal and shared "
            "contributions, and evolves missing capabilities with rollback gates."
        )
    )
    metadata: Dict[str, Any] = Field(
        default_factory=lambda: {
            "role": "website_builder",
            "orchestrator": True,
            "participatory_design": True,
        }
    )
    enable_evolving: bool = Field(default=True)

    def __init__(
        self,
        base_dir: str,
        prompt_name: Optional[str] = None,
        max_step: int = 180,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            base_dir=base_dir,
            prompt_name=prompt_name or "website_builder_agent",
            max_step=max_step,
            **kwargs,
        )


__all__ = ["WebsiteBuilderAgent"]
