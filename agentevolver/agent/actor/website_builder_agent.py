"""Website product engineer declared on the shared MetaAgent lifecycle.

The experiment declares subscribers and release obligations in its task manifest.
Task input, execution, feedback, adoption and completion use their owning managers.
"""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class WebsiteBuilderAgent(MetaAgent):
    """Website engineering role; orchestration and lifecycle are inherited."""

    name: str = Field(default="website_builder_agent")
    description: str = Field(default=(
        "An evolvable website product engineer that designs, implements, tests, "
        "deploys, and improves web products from task-defined requirements."
    ))
    metadata: Dict[str, Any] = Field(default_factory=lambda: {
        "role": "website_builder", "orchestrator": True, "product_engineering": True,
    })
    prompt_name: str = Field(default="website_builder_agent")
    max_step: int = Field(default=180)
    enable_evolving: bool = Field(default=True)
    # Browser sessions belong to dispatched users and reviewers, not the coordinator.
    capability_allowlists: Dict[str, List[str]] = Field(
        default_factory=lambda: {"environment": ["job"]},
    )


__all__ = ["WebsiteBuilderAgent"]
