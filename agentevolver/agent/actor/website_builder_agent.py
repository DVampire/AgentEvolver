"""Website product engineer declared on the shared MetaAgent lifecycle.

The experiment declares its browser mode and release obligations in its task manifest.
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
    # Traditional orchestration keeps a job-only scope. The single-builder demo
    # explicitly mounts browser_environment and receives its observations/images.
    capability_allowlists: Dict[str, List[str]] = Field(
        default_factory=lambda: {"environment": ["job"]},
    )

    async def prompt_modules(self, ctx):
        values = await super().prompt_modules(ctx)
        allowed = (getattr(ctx, "extra", None) or {}).get("environment_allowlist")
        values["direct_browser"] = (
            "browser_environment" in self.env_names
            and (allowed is None or "browser_environment" in allowed)
        )
        return values

    async def on_exit(self, status):
        try:
            if "browser_environment" in self.env_names:
                from agentevolver.environment.server import environment_manager

                environment = await environment_manager.get("browser_environment")
                if environment is not None:
                    await environment.close_session(str(getattr(self.ctx, "id", "") or "default"))
        finally:
            await super().on_exit(status)


__all__ = ["WebsiteBuilderAgent"]
