"""A solo game developer using the shared plan, capability and evolution lifecycle."""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class GameBuilderAgent(MetaAgent):
    name: str = Field(default="game_builder_agent")
    description: str = Field(default=(
        "A solo Godot game developer that designs, builds, plays and improves games, "
        "and evaluates reusable agent capabilities when actual development exposes a gap."
    ))
    metadata: Dict[str, Any] = Field(default={"role": "game_builder", "game_engine": "godot"})
    prompt_name: str = Field(default="game_builder_agent")
    include_agents: bool = Field(default=False)
    enable_evolving: bool = Field(default=True)
    max_step: int = Field(default=400)
    env_names: List[str] = Field(default_factory=lambda: ["job", "godot_environment", "browser_environment"])
    capability_allowlists: Dict[str, List[str]] = Field(default_factory=lambda: {
        "environment": ["job", "godot_environment", "browser_environment"], "agent": [],
    })
    max_screenshots: int = Field(default=1, ge=0)

    def attachments(self):
        from agentevolver.agent.actor.browser_agent import browser_images

        return [*super().attachments(), *browser_images(
            self._environment_observations.get("browser_environment"),
            max_screenshots=self.max_screenshots,
        )]

    async def on_exit(self, status):
        from agentevolver.environment.server import environment_manager
        from agentevolver.logger import logger

        try:
            for name in ("godot_environment", "browser_environment"):
                if name in self.env_names:
                    try:
                        environment = await environment_manager.get(name)
                        if environment is not None:
                            await environment.close_session(str(getattr(self.ctx, "id", "") or "default"))
                    except Exception as error:
                        logger.warning(f"GameBuilder could not close {name}: {error}")
        finally:
            await super().on_exit(status)


__all__ = ["GameBuilderAgent"]
